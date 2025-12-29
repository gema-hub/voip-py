import socket
import threading
import time
import base64
import json
import logging
import tkinter as tk
from tkinter import messagebox, ttk
import sys

# Configuración de Logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("VoIPClient")

# Dependencias Opcionales
try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False
    logger.warning("PyAudio no instalado. El audio no funcionará.")

try:
    import winsound
    WINSOUND_AVAILABLE = True
except ImportError:
    WINSOUND_AVAILABLE = False

class VoIPClient:
    def __init__(self, server_host, server_port, number, name, ui_callback):
        self.server_host = server_host
        self.server_port = int(server_port)
        self.number = number
        self.name = name or ""
        
        # UI Callbacks
        self.ui = ui_callback
        
        # Red
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", 0))
        self.local_port = self.sock.getsockname()[1]
        
        # Estado
        self.running = True
        self.connected = False
        self.peer = None
        self.in_call = False
        self.call_pending = False
        self.last_pong = time.time()
        
        # Audio Config
        self.audio_rate = 16000
        self.audio_chunk = 320  # 20ms
        self.audio_format = pyaudio.paInt16 if PYAUDIO_AVAILABLE else None
        self.audio_channels = 1
        
        # Objetos PyAudio
        self.p = None
        self.stream_in = None
        self.stream_out = None
        self.audio_thread = None
        
        # Hilos de control
        self._start_threads()

    def _start_threads(self):
        threading.Thread(target=self._listen_loop, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        threading.Thread(target=self._list_loop, daemon=True).start()

    def send(self, payload):
        try:
            self.sock.sendto(payload.encode(), (self.server_host, self.server_port))
        except Exception as e:
            logger.error(f"Error enviando paquete: {e}")

    def _listen_loop(self):
        while self.running:
            try:
                data, _ = self.sock.recvfrom(4096) # Buffer típico UDP
                msg = data.decode(errors="ignore").strip()
                if msg:
                    self._process_message(msg)
            except socket.error:
                if self.running:
                    time.sleep(0.1)
            except Exception as e:
                logger.error(f"Error en listen loop: {e}")

    def _process_message(self, msg):
        # Deduplicación simple (opcional)
        now = time.time()
        
        if msg == "OK":
            if not self.connected:
                self.connected = True
                self.ui.update_status("Conectado")
                self.ui.log("[SYSTEM] Conectado al servidor")

        elif msg == "PONG":
            self.last_pong = now
            if not self.connected:
                self.connected = True
                self.ui.update_status("Conectado")

        elif msg.startswith("LIST:"):
            users = msg[5:].split(",") if len(msg) > 5 else []
            count = len([u for u in users if u.strip()])
            self.ui.update_online_count(count)
            self.ui.log(f"[LIST] {count} usuarios online")

        elif msg.startswith("CALL_FROM:"):
            # Formato: CALL_FROM:caller:caller_name
            parts = msg.split(":", 2)
            if len(parts) >= 3:
                caller = parts[1]
                caller_name = parts[2]
                self._handle_incoming_call(caller, caller_name)

        elif msg.startswith("ACCEPT_FROM:"):
            # Formato: ACCEPT_FROM:callee
            callee = msg.split(":")[1]
            self._start_call_session(callee)
            self.ui.log(f"[CALL] Aceptada por {callee}")

        elif msg.startswith("RINGING_FROM:"):
            callee = msg.split(":")[1]
            self.ui.update_status(f"Llamando a {callee}...")
            self.ui.start_ringback()

        elif msg.startswith("REJECT_FROM:"):
            self._end_call("Rechazada")
            self.ui.log("[CALL] Rechazada")

        elif msg.startswith("BUSY_FROM:"):
            self._end_call("Ocupado")
            self.ui.log("[CALL] Línea ocupada")

        elif msg.startswith("OFFLINE:"):
            target = msg.split(":")[1]
            self._end_call("Offline")
            self.ui.log(f"[ERROR] {target} no encontrado")

        elif msg.startswith("OFFER_FROM_B64:"):
            # Formato: OFFER_FROM_B64:caller:base64payload
            parts = msg.split(":", 2)
            if len(parts) == 3:
                caller = parts[1]
                payload = parts[2]
                # En una implementación WebRTC real, aquí procesaríamos el SDP
                self.ui.log(f"[SIGNAL] Oferta WebRTC de {caller}")
                # Simulamos que la oferta es válida y aceptamos automáticamente si estamos en modo audio simple
                # Para este cliente UDP-Audio simple, ignoramos el WebRTC real y usamos el flujo de audio directo
                pass

        elif msg.startswith("AUDIO_FROM_B64:"):
            # Formato: AUDIO_FROM_B64:from:base64data
            parts = msg.split(":", 2)
            if len(parts) == 3:
                # No necesitamos el 'from' aquí porque ya sabemos con quien hablamos,
                # pero lo validamos por seguridad
                b64_data = parts[2]
                self._play_audio_chunk(b64_data)

        elif msg.startswith("BYE_FROM:"):
            frm = msg.split(":")[1]
            self._end_call("Finalizada")
            self.ui.log(f"[CALL] Terminada por {frm}")

    def _heartbeat_loop(self):
        while self.running:
            try:
                self.send(f"PING:{self.number}")
                time.sleep(10)
                if time.time() - self.last_pong > 30:
                    self.connected = False
                    self.ui.update_status("Desconectado")
                    # Re-registro silencioso
                    self._register()
            except Exception:
                pass

    def _list_loop(self):
        while self.running:
            if self.connected:
                self.send("LIST")
            time.sleep(20)

    def _register(self):
        self.ui.update_status("Conectando...")
        for _ in range(5):
            self.send(f"REGISTER:{self.number}:{self.local_port}:{self.name}")
            time.sleep(0.5)

    # --- Lógica de Llamada ---

    def call(self, number):
        if not number or self.in_call or self.call_pending:
            return
        
        self.peer = number
        self.call_pending = True
        self.ui.update_status(f"Llamando a {number}...")
        self.ui.start_ringback()
        self.send(f"CALL:{number}:{self.number}")
        
        # Timeout de llamada
        def timeout_check():
            time.sleep(30)
            if self.call_pending and not self.in_call:
                self.send(f"BYE:{self.peer}:{self.number}")
                self._end_call("Sin respuesta")
        
        threading.Thread(target=timeout_check, daemon=True).start()

    def accept(self, caller):
        self.send(f"ACCEPT:{caller}:{self.number}")
        self._start_call_session(caller)

    def reject(self, caller):
        self.send(f"REJECT:{caller}:{self.number}")
        self.ui.stop_ringtone()
        self.peer = None

    def hangup(self):
        if self.peer:
            self.send(f"BYE:{self.peer}:{self.number}")
        self._end_call("Colgada")

    def _handle_incoming_call(self, caller, name):
        if self.in_call or self.call_pending:
            self.send(f"BUSY:{caller}:{self.number}")
            return
        
        self.peer = caller
        self.ui.on_incoming_call(caller, name)

    def _start_call_session(self, peer_name):
        self.peer = peer_name
        self.in_call = True
        self.call_pending = False
        self.ui.stop_ringback()
        self.ui.stop_ringtone()
        self.ui.update_status("En llamada")
        self.ui.start_call_timer()
        self.ui.set_in_call_ui(True)
        
        # Iniciar Audio
        self._init_audio()

    def _end_call(self, reason):
        self.peer = None
        self.in_call = False
        self.call_pending = False
        self._stop_audio()
        self.ui.stop_ringback()
        self.ui.stop_ringtone()
        self.ui.stop_call_timer()
        self.ui.update_status("Listo")
        self.ui.set_in_call_ui(False)
        self.ui.log(f"[SYSTEM] Llamada finalizada: {reason}")

    # --- Audio ---

    def _init_audio(self):
        if not PYAUDIO_AVAILABLE:
            return
        
        try:
            self.p = pyaudio.PyAudio()
            
            # Input Stream
            self.stream_in = self.p.open(
                format=self.audio_format,
                channels=self.audio_channels,
                rate=self.audio_rate,
                input=True,
                frames_per_buffer=self.audio_chunk,
                stream_callback=self._audio_input_callback
            )
            
            # Output Stream
            self.stream_out = self.p.open(
                format=self.audio_format,
                channels=self.audio_channels,
                rate=self.audio_rate,
                output=True,
                frames_per_buffer=self.audio_chunk
            )
            
            self.stream_in.start_stream()
            logger.info("Audio streams iniciados")
            
        except Exception as e:
            logger.error(f"Error iniciando audio: {e}")
            self.ui.update_status("Error Audio")

    def _audio_input_callback(self, in_data, frame_count, time_info, status):
        """Callback de PyAudio para capturar micrófono sin bloquear."""
        if self.in_call and not self.ui.muted:
            try:
                # Enviamos raw data codificado en base64
                b64 = base64.b64encode(in_data).decode()
                # Enviamos fragmentos UDP
                self.send(f"AUDIO_B64:{self.peer}:{self.number}:{b64}")
            except Exception:
                pass
        return (None, pyaudio.paContinue)

    def _play_audio_chunk(self, b64_data):
        if not self.in_call or not self.ui.speaker_on:
            return
        
        try:
            data = base64.b64decode(b64_data)
            if self.stream_out and self.stream_out.is_active():
                self.stream_out.write(data)
        except Exception as e:
            # logger.debug(f"Error reproduciendo audio: {e}") # Silenciar para no saturar logs
            pass

    def _stop_audio(self):
        if self.stream_in:
            try:
                self.stream_in.stop_stream()
                self.stream_in.close()
            except: pass
            self.stream_in = None
            
        if self.stream_out:
            try:
                self.stream_out.stop_stream()
                self.stream_out.close()
            except: pass
            self.stream_out = None
            
        if self.p:
            try:
                self.p.terminate()
            except: pass
            self.p = None

    def close(self):
        self.running = False
        self.hangup()
        self.send(f"UNREGISTER:{self.number}")
        try:
            self.sock.close()
        except: pass

# ================= UI (Tkinter) =================

class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("VoIP Pro Client v2")
        self.root.geometry("400x650")
        self.root.resizable(False, False)
        
        # Estilo Oscuro
        self.colors = {
            "bg": "#121212",
            "fg": "#E0E0E0",
            "accent": "#00E676", # Verde brillante
            "danger": "#FF5252", # Rojo
            "btn": "#2C2C2C",
            "input": "#1E1E1E"
        }
        self.root.configure(bg=self.colors["bg"])
        
        # Variables de estado
        self.client = None
        self.muted = False
        self.speaker_on = True
        self.ringtone_active = False
        self.ringback_active = False
        self.sound_enabled = True
        
        self._setup_styles()
        self._build_ui()
        
        # Configuración inicial (por defecto)
        self.server_host = "jacob.hidencloud.com"
        self.server_port = "24646"
        
        # Checkear pyaudio
        if not PYAUDIO_AVAILABLE:
            messagebox.showwarning("Advertencia", "PyAudio no está instalado. No podrás realizar ni recibir llamadas con audio.")

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        
        style.configure("TFrame", background=self.colors["bg"])
        style.configure("TLabel", background=self.colors["bg"], foreground=self.colors["fg"], font=("Segoe UI", 10))
        style.configure("Header.TLabel", font=("Segoe UI", 18, "bold"), foreground=self.colors["accent"])
        
        style.configure("TButton", font=("Segoe UI", 10, "bold"), padding=6)
        style.map("TButton", background=[("active", "#3D3D3D")])
        style.configure("Call.TButton", background=self.colors["accent"], foreground="black")
        style.configure("Hang.TButton", background=self.colors["danger"], foreground="white")

    def _build_ui(self):
        # Header
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=10, pady=10)
        
        ttk.Label(header, text="VoIP Pro", style="Header.TLabel").pack(side="left")
        self.status_lbl = ttk.Label(header, text="Desconectado", foreground="#AAAAAA")
        self.status_lbl.pack(side="right")
        
        # Info Usuario
        self.user_frame = ttk.Frame(self.root)
        self.user_frame.pack(fill="x", padx=10, pady=5)
        self.my_number_lbl = ttk.Label(self.user_frame, text="N/D", font=("Segoe UI", 12, "bold"))
        self.my_number_lbl.pack()
        self.online_lbl = ttk.Label(self.user_frame, text="En línea: 0")
        self.online_lbl.pack()

        # Display Número
        self.number_var = tk.StringVar()
        entry = ttk.Entry(self.root, textvariable=self.number_var, font=("Segoe UI", 24, "bold"), justify="center")
        entry.pack(padx=20, pady=10, fill="x")

        # Teclado
        keypad = ttk.Frame(self.root)
        keypad.pack(pady=10)
        
        keys = [
            ("1", "2", "3"),
            ("4", "5", "6"),
            ("7", "8", "9"),
            ("*", "0", "#")
        ]
        
        for row in keys:
            r_frame = ttk.Frame(keypad)
            r_frame.pack()
            for k in row:
                btn = ttk.Button(r_frame, text=k, width=5, command=lambda x=k: self._append(x))
                btn.pack(side="left", padx=5, pady=5)

        # Controles de Llamada
        ctrl_frame = ttk.Frame(self.root)
        ctrl_frame.pack(pady=15)
        
        ttk.Button(ctrl_frame, text="Borrar", command=self._backspace).pack(side="left", padx=5)
        self.call_btn = ttk.Button(ctrl_frame, text="LLAMAR", style="Call.TButton", command=self._on_call)
        self.call_btn.pack(side="left", padx=10)
        self.hangup_btn = ttk.Button(ctrl_frame, text="COLGAR", style="Hang.TButton", state="disabled", command=self._on_hangup)
        self.hangup_btn.pack(side="left", padx=10)
        ttk.Button(ctrl_frame, text="Limpiar", command=lambda: self.number_var.set("")).pack(side="left", padx=5)

        # Barra de Progreso / Info
        self.info_frame = ttk.Frame(self.root)
        self.info_frame.pack(fill="x", padx=10, pady=10)
        
        self.timer_lbl = ttk.Label(self.info_frame, text="00:00", font=("Consolas", 14))
        self.timer_lbl.pack()
        
        # Botones Inferiores
        bottom = ttk.Frame(self.root)
        bottom.pack(side="bottom", fill="x", pady=10)
        
        ttk.Button(bottom, text="Ajustes", command=self._open_settings).pack(side="left", padx=10)
        ttk.Button(bottom, text="Salir", command=self._on_exit).pack(side="right", padx=10)

        # Botones Mute/Speaker (ocultos inicialmente)
        self.audio_ctrl_frame = ttk.Frame(self.root)
        # Se empaquetarán al iniciar llamada
        
        self.mute_btn = ttk.Button(self.audio_ctrl_frame, text="Mic: ON", command=self._toggle_mute)
        self.mute_btn.pack(side="left", padx=10)
        self.spk_btn = ttk.Button(self.audio_ctrl_frame, text="Spk: ON", command=self._toggle_speaker)
        self.spk_btn.pack(side="left", padx=10)

    # --- Funciones UI ---
    
    def _append(self, char):
        self.number_var.set(self.number_var.get() + char)

    def _backspace(self):
        curr = self.number_var.get()
        self.number_var.set(curr[:-1])

    def _on_call(self):
        num = self.number_var.get().strip()
        if self.client:
            self.client.call(num)

    def _on_hangup(self):
        if self.client:
            self.client.hangup()

    def _toggle_mute(self):
        self.muted = not self.muted
        text = "Mic: OFF" if self.muted else "Mic: ON"
        self.mute_btn.configure(text=text)

    def _toggle_speaker(self):
        self.speaker_on = not self.speaker_on
        text = "Spk: OFF" if self.speaker_on else "Spk: ON"
        self.spk_btn.configure(text=text)

    def update_status(self, text):
        self.root.after(0, lambda: self.status_lbl.configure(text=text))

    def update_online_count(self, n):
        self.root.after(0, lambda: self.online_lbl.configure(text=f"En línea: {n}"))

    def log(self, msg):
        # Simple log a consola, podría ir a una ventana
        logger.info(msg)

    def set_in_call_ui(self, in_call):
        def _update():
            if in_call:
                self.call_btn.state(['disabled'])
                self.hangup_btn.state(['!disabled'])
                self.audio_ctrl_frame.pack(after=self.info_frame, pady=5)
            else:
                self.call_btn.state(['!disabled'])
                self.hangup_btn.state(['disabled'])
                self.audio_ctrl_frame.pack_forget()
                self.timer_lbl.configure(text="00:00")
        self.root.after(0, _update)

    def start_call_timer(self):
        self.seconds = 0
        def tick():
            if self.client and self.client.in_call:
                self.seconds += 1
                m, s = divmod(self.seconds, 60)
                self.timer_lbl.configure(text=f"{m:02d}:{s:02d}")
                self.root.after(1000, tick)
        self.root.after(0, tick)

    def stop_call_timer(self):
        pass # El loop se detiene solo al verificar client.in_call

    # --- Ajustes y Conexion ---

    def _open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Configuración")
        win.geometry("300x250")
        win.configure(bg=self.colors["bg"])
        win.transient(self.root)
        win.grab_set()

        ttk.Label(win, text="Número de Usuario:").pack(pady=5)
        num_var = tk.StringVar(value=self.client.number if self.client else "1001")
        ttk.Entry(win, textvariable=num_var).pack(pady=5)
        
        ttk.Label(win, text="Nombre (Opcional):").pack(pady=5)
        name_var = tk.StringVar(value=self.client.name if self.client else "User")
        ttk.Entry(win, textvariable=name_var).pack(pady=5)
        
        def save():
            n = num_var.get().strip()
            nm = name_var.get().strip()
            if n:
                self._connect(n, nm)
                win.destroy()
        
        ttk.Button(win, text="Conectar", command=save).pack(pady=15)

    def _connect(self, number, name):
        if self.client:
            self.client.close()
        
        self.my_number_lbl.configure(text=f"Usuario: {number}")
        self.client = VoIPClient(self.server_host, self.server_port, number, name, self)
        self.client._register()

    # --- Ventanas de Llamada ---

    def on_incoming_call(self, caller, name):
        # Mostrar ventana flotante
        if hasattr(self, 'incoming_win') and self.incoming_win and self.incoming_win.winfo_exists():
            return
            
        self.start_ringtone()
        
        self.incoming_win = tk.Toplevel(self.root)
        self.incoming_win.title("Llamada Entrante")
        self.incoming_win.geometry("300x200")
        self.incoming_win.configure(bg=self.colors["bg"])
        self.incoming_win.attributes('-topmost', True)
        
        ttk.Label(self.incoming_win, text=f"LLAMADA DE", background=self.colors["bg"]).pack(pady=10)
        ttk.Label(self.incoming_win, text=name or caller, style="Header.TLabel", background=self.colors["bg"]).pack(pady=10)
        
        btns = ttk.Frame(self.incoming_win)
        btns.pack(pady=20)
        
        def acc():
            self.stop_ringtone()
            self.client.accept(caller)
            self.incoming_win.destroy()
            
        def rej():
            self.stop_ringtone()
            self.client.reject(caller)
            self.incoming_win.destroy()
            
        ttk.Button(btns, text="Aceptar", style="Call.TButton", command=acc).pack(side="left", padx=10)
        ttk.Button(btns, text="Rechazar", style="Hang.TButton", command=rej).pack(side="left", padx=10)

    # --- Sonidos (Hilos) ---

    def start_ringtone(self):
        if self.ringtone_active or not WINSOUND_AVAILABLE: return
        self.ringtone_active = True
        threading.Thread(target=self._play_ringtone_loop, daemon=True).start()

    def stop_ringtone(self):
        self.ringtone_active = False

    def start_ringback(self):
        if self.ringback_active or not WINSOUND_AVAILABLE: return
        self.ringback_active = True
        threading.Thread(target=self._play_ringback_loop, daemon=True).start()

    def stop_ringback(self):
        self.ringback_active = False

    def _play_ringtone_loop(self):
        # Patrón: Beep alto - Beep alto - Pausa
        while self.ringtone_active:
            try:
                winsound.Beep(600, 300)
                time.sleep(0.2)
                winsound.Beep(600, 300)
                time.sleep(1.5)
            except:
                break

    def _play_ringback_loop(self):
        # Patrón: Beep bajo único repetitivo
        while self.ringback_active:
            try:
                winsound.Beep(450, 400)
                time.sleep(2.5)
            except:
                break

    def _on_exit(self):
        if self.client:
            self.client.close()
        self.root.destroy()
        sys.exit(0)

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = App()
    app.run()