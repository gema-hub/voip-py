import socket
import threading
import time
import hashlib

HOST = "0.0.0.0"
PORT = 24646

clients = {}  # {number: (ip, port, last_seen, name)}
recent = {}   # {(addr, hash): last_time}
claimed = {}  # {number: claimed_port}

lock = threading.Lock()

def _send_redundant_bytes(sock, addr, data, copies=2, delay=0.01):
    ok = False
    for i in range(max(1, copies)):
        try:
            sock.sendto(data, addr)
            ok = True
        except Exception as e:
            print(f"[ERR] send to {addr}: {e}")
        if copies > 1 and i < copies - 1:
            time.sleep(delay)
    return ok

def _send_ok(sock, addr):
    return _send_redundant_bytes(sock, addr, b"OK", copies=2, delay=0.02)

def forward(to_number, payload, sock):
    info = clients.get(to_number)
    if info:
        ip, port, _, _ = info
        try:
            _send_redundant_bytes(sock, (ip, port), payload.encode(), copies=2, delay=0.02)
            cport = claimed.get(to_number, 0)
            if isinstance(cport, int) and cport > 0 and cport != port:
                _send_redundant_bytes(sock, (ip, cport), payload.encode(), copies=2, delay=0.02)
            return True
        except Exception as e:
            print(f"[ERR] forward to {to_number}: {e}")
    return False


def cleanup():
    while True:
        time.sleep(30)
        now = time.time()
        with lock:
            expired = [n for n, (_, _, t, _) in clients.items() if now - t > 60]
            for n in expired:
                print(f"[OFFLINE] {n}")
                del clients[n]


def handle(data, addr, sock):
    try:
        if len(data) > 65535:
            return
        msg = data.decode(errors="ignore").strip()
        print(f"[REC] {msg} from {addr}")
    except:
        return

    try:
        h = hashlib.sha256((str(addr) + "|" + msg).encode()).hexdigest()
        now = time.time()
        last = recent.get((addr, h), 0)
        if now - last < 0.3:
            return
        recent[(addr, h)] = now
    except:
        pass

    parts = msg.split(":")
    cmd = parts[0]

    with lock:
        if cmd == "REGISTER":
            number = parts[1]
            claimed_port = int(parts[2]) if len(parts) >= 3 else 0
            name = parts[3] if len(parts) >= 4 else ""
            number = (number or "")[:32]
            name = (name or "")[:32]
            clients[number] = (addr[0], addr[1], time.time(), name)
            try:
                claimed[number] = claimed_port
            except Exception:
                pass
            print(f"[ONLINE] {number} ({name}) -> {addr[0]}:{addr[1]} (claimed {claimed_port})")
            _send_ok(sock, addr)

        elif cmd == "PING":
            number = parts[1] if len(parts) >= 2 else ""
            number = (number or "")[:32]
            if number in clients:
                ip, port, _, name = clients[number]
                clients[number] = (ip, port, time.time(), name)
            else:
                clients[number] = (addr[0], addr[1], time.time(), "")
                print(f"[ONLINE_AUTO] {number} -> {addr[0]}:{addr[1]}")
            try:
                _send_redundant_bytes(sock, addr, b"PONG", copies=2, delay=0.02)
            except Exception as e:
                print(f"[ERR] PONG to {addr}: {e}")

        elif cmd == "CALL":
            callee = parts[1] if len(parts) >= 2 else ""
            caller = parts[2] if len(parts) >= 3 else ""
            callee = (callee or "")[:32]
            caller = (caller or "")[:32]
            print(f"[CALL] {caller} -> {callee}")
            caller_name = ""
            if caller in clients:
                _, _, _, caller_name = clients.get(caller, ("", 0, 0, ""))
            sent = forward(callee, f"CALL_FROM:{caller}:{caller_name}", sock)
            try:
                if sent:
                    _send_ok(sock, addr)
                    _send_redundant_bytes(sock, addr, f"RINGING_FROM:{callee}".encode(), copies=2, delay=0.02)
                else:
                    _send_redundant_bytes(sock, addr, f"OFFLINE:{callee}".encode(), copies=2, delay=0.02)
                    print(f"[MISS] callee {callee} no registrado")
            except Exception as e:
                print(f"[ERR] ACK CALL to {addr}: {e}")

        elif cmd == "ACCEPT":
            caller = parts[1] if len(parts) >= 2 else ""
            callee = parts[2] if len(parts) >= 3 else ""
            caller = (caller or "")[:32]
            callee = (callee or "")[:32]
            print(f"[ACCEPT] {callee} -> {caller}")
            sent = forward(caller, f"ACCEPT_FROM:{callee}", sock)
            try:
                if sent:
                    _send_ok(sock, addr)
                else:
                    _send_redundant_bytes(sock, addr, f"OFFLINE:{caller}".encode(), copies=2, delay=0.02)
                    print(f"[MISS] caller {caller} no registrado")
            except Exception as e:
                print(f"[ERR] ACK ACCEPT to {addr}: {e}")

        elif cmd == "REJECT":
            caller = parts[1] if len(parts) >= 2 else ""
            callee = parts[2] if len(parts) >= 3 else ""
            caller = (caller or "")[:32]
            callee = (callee or "")[:32]
            print(f"[REJECT] {callee} -> {caller}")
            sent = forward(caller, f"REJECT_FROM:{callee}", sock)
            try:
                if sent:
                    _send_ok(sock, addr)
                else:
                    _send_redundant_bytes(sock, addr, f"OFFLINE:{caller}".encode(), copies=2, delay=0.02)
                    print(f"[MISS] caller {caller} no registrado")
            except Exception as e:
                print(f"[ERR] ACK REJECT to {addr}: {e}")

        elif cmd == "BUSY":
            caller = parts[1] if len(parts) >= 2 else ""
            callee = parts[2] if len(parts) >= 3 else ""
            caller = (caller or "")[:32]
            callee = (callee or "")[:32]
            print(f"[BUSY] {callee} -> {caller}")
            sent = forward(caller, f"BUSY_FROM:{callee}", sock)
            try:
                if sent:
                    _send_ok(sock, addr)
                else:
                    _send_redundant_bytes(sock, addr, f"OFFLINE:{caller}".encode(), copies=2, delay=0.02)
                    print(f"[MISS] caller {caller} no registrado")
            except Exception as e:
                print(f"[ERR] ACK BUSY to {addr}: {e}")

        elif cmd in ("OFFER_B64", "WEBRTC_OFFER_B64"):
            # OFFER_B64:callee:caller:<b64>
            if len(parts) >= 4:
                callee = parts[1]
                caller = parts[2]
                b64 = ":".join(parts[3:])  # por si hay ':' en base64
                callee = (callee or "")[:32]
                caller = (caller or "")[:32]
                print(f"[OFFER] {caller} -> {callee}")
                sent = forward(callee, f"OFFER_FROM_B64:{caller}:{b64}", sock)
                if sent:
                    _send_ok(sock, addr)
                else:
                    _send_redundant_bytes(sock, addr, f"OFFLINE:{callee}".encode(), copies=2, delay=0.02)
                    print(f"[MISS] callee {callee} no registrado")
            else:
                print("[OFFER] malformed")
                _send_redundant_bytes(sock, addr, b"ERR", copies=2, delay=0.02)

        elif cmd in ("ANSWER_B64", "WEBRTC_ANSWER_B64"):
            # ANSWER_B64:caller:callee:<b64>
            if len(parts) >= 4:
                caller = parts[1]
                callee = parts[2]
                b64 = ":".join(parts[3:])
                caller = (caller or "")[:32]
                callee = (callee or "")[:32]
                print(f"[ANSWER] {callee} -> {caller}")
                sent = forward(caller, f"ANSWER_FROM_B64:{callee}:{b64}", sock)
                if sent:
                    _send_ok(sock, addr)
                else:
                    _send_redundant_bytes(sock, addr, f"OFFLINE:{caller}".encode(), copies=2, delay=0.02)
                    print(f"[MISS] caller {caller} no registrado")
            else:
                print("[ANSWER] malformed")
                _send_redundant_bytes(sock, addr, b"ERR", copies=2, delay=0.02)

        elif cmd in ("ICE_B64", "WEBRTC_ICE_B64"):
            # ICE_B64:to:from:<b64>
            if len(parts) >= 4:
                to = parts[1]
                frm = parts[2]
                b64 = ":".join(parts[3:])
                to = (to or "")[:32]
                frm = (frm or "")[:32]
                print(f"[ICE] {frm} -> {to}")
                sent = forward(to, f"ICE_FROM_B64:{frm}:{b64}", sock)
                if sent:
                    _send_ok(sock, addr)
                else:
                    _send_redundant_bytes(sock, addr, f"OFFLINE:{to}".encode(), copies=2, delay=0.02)
                    print(f"[MISS] to {to} no registrado")
            else:
                print("[ICE] malformed")
                _send_redundant_bytes(sock, addr, b"ERR", copies=2, delay=0.02)

        elif cmd in ("AUDIO_B64",):
            if len(parts) >= 4:
                to = parts[1]
                frm = parts[2]
                b64 = ":".join(parts[3:])
                to = (to or "")[:32]
                frm = (frm or "")[:32]
                sent = forward(to, f"AUDIO_FROM_B64:{frm}:{b64}", sock)
                if sent:
                    _send_ok(sock, addr)
                else:
                    _send_redundant_bytes(sock, addr, f"OFFLINE:{to}".encode(), copies=2, delay=0.02)
            else:
                _send_redundant_bytes(sock, addr, b"ERR", copies=2, delay=0.02)

        elif cmd in ("BYE", "HANGUP"):
            to = parts[1] if len(parts) >= 2 else ""
            frm = parts[2] if len(parts) >= 3 else ""
            to = (to or "")[:32]
            frm = (frm or "")[:32]
            print(f"[BYE] {frm} -> {to}")
            forward(to, f"BYE_FROM:{frm}", sock)
            try:
                _send_ok(sock, addr)
            except Exception as e:
                print(f"[ERR] ACK BYE to {addr}: {e}")

        elif cmd == "LIST":
            entries = []
            for n, (_, _, _, nm) in clients.items():
                nm = nm or ""
                entries.append(f"{n}|{nm}")
            online = ",".join(entries)
            try:
                _send_redundant_bytes(sock, addr, f"LIST:{online}".encode(), copies=2, delay=0.02)
            except Exception as e:
                print(f"[ERR] LIST to {addr}: {e}")

        elif cmd == "UNREGISTER":
            number = parts[1] if len(parts) >= 2 else ""
            number = (number or "")[:32]
            if number in clients:
                del clients[number]
            try:
                _send_ok(sock, addr)
            except Exception as e:
                print(f"[ERR] ACK UNREGISTER to {addr}: {e}")
        else:
            try:
                _send_redundant_bytes(sock, addr, b"ERR", copies=2, delay=0.02)
            except Exception as e:
                print(f"[ERR] Unknown cmd to {addr}: {e}")


def start():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST, PORT))
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
    except Exception as e:
        print(f"[WARN] set sock buffers: {e}")
    print(f"Servidor VoIP (reenviando señales) en {HOST}:{PORT}")

    threading.Thread(target=cleanup, daemon=True).start()

    while True:
        data, addr = sock.recvfrom(65535)
        threading.Thread(target=handle, args=(data, addr, sock), daemon=True).start()


if __name__ == "__main__":
    start()
