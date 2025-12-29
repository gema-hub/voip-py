import socket
import threading
import time
import hashlib
import logging
import signal
import sys
from concurrent.futures import ThreadPoolExecutor

# ================= CONFIGURACIÓN =================
HOST = "0.0.0.0"
PORT = 24646
MAX_WORKERS = 50           # Máximo de hilos simultáneos para procesar paquetes
CLIENT_TIMEOUT = 60        # Segundos para eliminar un cliente inactivo
CLEANUP_INTERVAL = 30      # Frecuencia de limpieza (segundos)
PACKET_RESEND_DELAY = 0.02 # Retraso entre reenvíos redundantes (segundos)
PACKET_RESEND_COUNT = 2    # Cantidad de reenvíos por paquete

# ================= ESTADO GLOBAL =================
# clients: {number: (ip, port, last_seen, name)}
clients = {}
# claimed: {number: claimed_port} (Para NAT traversal)
claimed_ports = {}
# recent: {(addr, hash): last_time} (Anti-duplicados)
recent = {}

lock = threading.RLock()
running = True
sock = None

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("VoIPServer")

# ================= FUNCIONES DE RED =================

def _send_redundant_bytes(target_sock, addr, data, copies=PACKET_RESEND_COUNT, delay=PACKET_RESEND_DELAY):
    """Envía datos varias veces para asegurar entrega sobre UDP."""
    if not running: return False
    success = False
    data_bytes = data if isinstance(data, bytes) else data.encode()
    
    for i in range(max(1, copies)):
        try:
            target_sock.sendto(data_bytes, addr)
            success = True
        except OSError as e:
            logger.error(f"[SEND ERROR] a {addr}: {e}")
            # Si el socket está cerrado, no intentamos más
            if e.errno == 9: # Bad file descriptor
                return False
        except Exception as e:
            logger.debug(f"[SEND WARN] a {addr}: {e}")
        
        if copies > 1 and i < copies - 1:
            time.sleep(delay)
            
    return success

def _send_ok(target_sock, addr):
    return _send_redundant_bytes(target_sock, addr, b"OK")

def forward(target_sock, to_number, payload):
    """Reenvía payload al destinatario, intentando puertos alternativos si existen."""
    info = clients.get(to_number)
    if not info:
        return False
    
    ip, port, _, _ = info
    
    # 1. Enviar al puerto registrado principal
    sent = _send_redundant_bytes(target_sock, (ip, port), payload)
    
    # 2. Enviar al puerto "reclamado" (NAT Traversal helper) si es diferente
    cport = claimed_ports.get(to_number)
    if isinstance(cport, int) and cport > 0 and cport != port:
        logger.debug(f"[NAT] Reenviando a puerto alternativo {cport} para {to_number}")
        _send_redundant_bytes(target_sock, (ip, cport), payload)
        
    return sent

# ================= LÓGICA DE NEGOCIO =================

def cleanup_task():
    """Hilo de fondo que elimina clientes inactivos."""
    logger.info("Hilo de limpieza iniciado.")
    while running:
        time.sleep(CLEANUP_INTERVAL)
        now = time.time()
        with lock:
            expired_numbers = [
                n for n, (_, _, t, _) in clients.items() 
                if now - t > CLIENT_TIMEOUT
            ]
            for n in expired_numbers:
                logger.info(f"[TIMEOUT] Cliente {n} eliminado por inactividad.")
                del clients[n]
                if n in claimed_ports:
                    del claimed_ports[n]

def get_msg_hash(msg_str, addr):
    """Genera un hash único para evitar procesar el mismo mensaje repetido."""
    # Usamos un hash rápido combinando dirección y mensaje
    return hashlib.sha256((str(addr) + msg_str).encode()).hexdigest()

def safe_split(msg, maxsplit=-1):
    """Divide el mensaje de forma segura."""
    if not msg:
        return []
    return msg.split(":", maxsplit)

def process_message(data, addr, server_sock):
    """Procesa un paquete UDP individual. (Ejecutado en un ThreadPool)."""
    try:
        if len(data) > 65535: return # MTU safety
        
        msg = data.decode(errors="ignore").strip()
        if not msg: return
        
        # Deduplicación (Anti-spam/replay)
        h = get_msg_hash(msg, addr)
        now = time.time()
        with lock:
            last_time = recent.get((addr, h), 0)
            if now - last_time < 0.3:
                return # Duplicado reciente
            recent[(addr, h)] = now
        
        # Limpieza periódica del diccionario de recientes (simple)
        if len(recent) > 10000:
            with lock:
                old_keys = [k for k, v in recent.items() if now - v > 5]
                for k in old_keys: del recent[k]

        # Parsing
        parts = safe_split(msg, 3) # Limitamos split inicial a 4 partes para eficiencia
        if not parts: return
        cmd = parts[0]

        # --- COMANDOS ---
        
        if cmd == "REGISTER":
            # Formato: REGISTER:number:claimed_port:name
            if len(parts) < 2: return
            number = parts[1][:32].strip()
            claimed_port = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            name = parts[3][:32].strip() if len(parts) > 3 else ""
            
            if not number: return

            with lock:
                clients[number] = (addr[0], addr[1], time.time(), name)
                if claimed_port > 0:
                    claimed_ports[number] = claimed_port
                elif number in claimed_ports:
                    del claimed_ports[number]
            
            logger.info(f"[REGISTER] {number} ({name}) en {addr[0]}:{addr[1]} (AltPort: {claimed_port})")
            _send_ok(server_sock, addr)

        elif cmd == "PING":
            # Formato: PING:number
            if len(parts) < 2: return
            number = parts[1][:32].strip()
            
            with lock:
                if number in clients:
                    ip, port, _, name = clients[number]
                    # Si la IP cambió, actualizamos (soporte básico de IP móvil)
                    clients[number] = (addr[0], addr[1], time.time(), name)
                else:
                    # Auto-registro si no existe
                    clients[number] = (addr[0], addr[1], time.time(), "")
            
            _send_redundant_bytes(server_sock, addr, b"PONG")

        elif cmd == "CALL":
            # Formato: CALL:callee:caller
            if len(parts) < 3: return
            callee, caller = parts[1][:32], parts[2][:32]
            
            logger.info(f"[CALL] Intento de {caller} -> {callee}")
            
            caller_name = ""
            with lock:
                if caller in clients:
                    caller_name = clients[caller][3]

            # Notificamos al que recibe la llamada
            sent = forward(server_sock, callee, f"CALL_FROM:{caller}:{caller_name}")
            
            if sent:
                _send_ok(server_sock, addr)
                _send_redundant_bytes(server_sock, addr, f"RINGING_FROM:{callee}".encode())
            else:
                _send_redundant_bytes(server_sock, addr, f"OFFLINE:{callee}".encode())
                logger.warning(f"[CALL FALLIDO] Destino {callee} no encontrado.")

        elif cmd in ("ACCEPT", "REJECT", "BUSY"):
            # Formato: CMD:caller:callee
            if len(parts) < 3: return
            target, source = parts[1][:32], parts[2][:32]
            
            logger.info(f"[{cmd}] {source} -> {target}")
            sent = forward(server_sock, target, f"{cmd}_FROM:{source}")
            
            if sent:
                _send_ok(server_sock, addr)
            else:
                _send_redundant_bytes(server_sock, addr, f"OFFLINE:{target}".encode())

        elif cmd in ("OFFER_B64", "WEBRTC_OFFER_B64"):
            # Formato: OFFER_B64:callee:caller:<b64_data>
            # Nota: partes[3] puede contener ':', por eso re-unimos el resto si es necesario
            if len(parts) >= 4:
                callee = parts[1][:32]
                caller = parts[2][:32]
                b64_payload = ":".join(parts[3:]) # Unir todo lo demás por si el b64 tiene :
                
                logger.debug(f"[OFFER] {caller} -> {callee} ({len(b64_payload)} bytes)")
                if forward(server_sock, callee, f"OFFER_FROM_B64:{caller}:{b64_payload}"):
                    _send_ok(server_sock, addr)
                else:
                    _send_redundant_bytes(server_sock, addr, f"OFFLINE:{callee}".encode())
            else:
                _send_redundant_bytes(server_sock, addr, b"ERR_MALFORMED")

        elif cmd in ("ANSWER_B64", "WEBRTC_ANSWER_B64"):
            # Formato: ANSWER_B64:caller:callee:<b64_data>
            if len(parts) >= 4:
                caller = parts[1][:32]
                callee = parts[2][:32]
                b64_payload = ":".join(parts[3:])
                
                logger.debug(f"[ANSWER] {callee} -> {caller} ({len(b64_payload)} bytes)")
                if forward(server_sock, caller, f"ANSWER_FROM_B64:{callee}:{b64_payload}"):
                    _send_ok(server_sock, addr)
                else:
                    _send_redundant_bytes(server_sock, addr, f"OFFLINE:{caller}".encode())

        elif cmd in ("ICE_B64", "WEBRTC_ICE_B64"):
            # Formato: ICE_B64:to:from:<b64_data>
            if len(parts) >= 4:
                to = parts[1][:32]
                frm = parts[2][:32]
                b64_payload = ":".join(parts[3:])
                
                # Silenciar log de ICE para no saturar consola
                # logger.debug(f"[ICE] {frm} -> {to}")
                if forward(server_sock, to, f"ICE_FROM_B64:{frm}:{b64_payload}"):
                    _send_ok(server_sock, addr)
                else:
                    _send_redundant_bytes(server_sock, addr, f"OFFLINE:{to}".encode())

        elif cmd == "AUDIO_B64":
            # Formato: AUDIO_B64:to:from:<data>
            if len(parts) >= 4:
                to = parts[1][:32]
                frm = parts[2][:32]
                b64_payload = ":".join(parts[3:])
                forward(server_sock, to, f"AUDIO_FROM_B64:{frm}:{b64_payload}")

        elif cmd in ("BYE", "HANGUP"):
            # Formato: BYE:to:from
            if len(parts) >= 3:
                to = parts[1][:32]
                frm = parts[2][:32]
                logger.info(f"[BYE] {frm} -> {to}")
                forward(server_sock, to, f"BYE_FROM:{frm}")
                _send_ok(server_sock, addr)

        elif cmd == "LIST":
            # Devuelve lista de usuarios online
            entries = []
            with lock:
                for n, (_, _, _, nm) in clients.items():
                    entries.append(f"{n}|{nm}")
            online_csv = ",".join(entries)
            _send_redundant_bytes(server_sock, addr, f"LIST:{online_csv}".encode())

        elif cmd == "UNREGISTER":
            if len(parts) >= 2:
                number = parts[1][:32]
                with lock:
                    if number in clients:
                        del clients[number]
                        if number in claimed_ports:
                            del claimed_ports[number]
                        logger.info(f"[UNREGISTER] {number} se ha ido.")
                _send_ok(server_sock, addr)
        
        else:
            # Comando desconocido
            logger.debug(f"[UNKNOWN CMD] {cmd} desde {addr}")
            _send_redundant_bytes(server_sock, addr, b"ERR_CMD")

    except Exception as e:
        logger.error(f"[CRITICAL ERROR] Procesando paquete de {addr}: {e}")

# ================= INICIO Y SERVIDOR =================

def signal_handler(sig, frame):
    """Manejo de señal de terminación (Ctrl+C)."""
    global running
    logger.info("Señal de terminación recibida. Cerrando servidor...")
    running = False
    if sock:
        sock.close()
    sys.exit(0)

def start_server():
    global sock
    # Registrar señales para cierre limpio
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Configuración del Socket UDP
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((HOST, PORT))
        
        # Intentar aumentar buffers (puede fallar en algunos SO sin permisos)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024) # 1MB
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024) # 1MB
        except OSError as e:
            logger.warning(f"No se pudo ajustar el tamaño del buffer del socket: {e}")
            
        logger.info(f"*** Servidor VoIP UDP iniciado en {HOST}:{PORT} ***")
        logger.info(f"Pool de Hilos: {MAX_WORKERS} trabajadores. Timeout cliente: {CLIENT_TIMEOUT}s")

    except Exception as e:
        logger.error(f"Error fatal al iniciar socket: {e}")
        sys.exit(1)

    # Iniciar hilo de limpieza
    cleanup_thread = threading.Thread(target=cleanup_task, daemon=True)
    cleanup_thread.start()

    # Pool de hilos para procesar mensajes
    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="VoIPWorker") as executor:
        logger.info("Listo para recibir conexiones...")
        
        while running:
            try:
                # recvfrom es bloqueante
                data, addr = sock.recvfrom(65535)
                if running:
                    # Despachar la tarea al pool de hilos
                    executor.submit(process_message, data, addr, sock)
            except OSError:
                # Se lanza cuando el socket se cierra (running = False)
                if running:
                    logger.error("Error en socket.recvfrom. Reiniciando...")
                break
            except Exception as e:
                logger.error(f"Error inesperado en el bucle principal: {e}")

    logger.info("Servidor detenido.")

if __name__ == "__main__":
    start_server()