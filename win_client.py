import socket
import threading
import time
import base64
import json
import tkinter as tk
from tkinter import messagebox
import winsound
try:
    import pyaudio
except Exception:
    pyaudio = None

class Client:
    def __init__(self, server_host, server_port, number, name, ui=None):
        self.server_host = server_host
        self.server_port = int(server_port)
        self.number = number
        self.name = name or ""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", 0))
        self.local_port = self.sock.getsockname()[1]
        self.connected = False
        self.peer = None
        self.running = True
        self.status = "Listo"
        self.ui = ui
        self.audio_enabled = True
        self.audio_running = False
        self.audio_in = None
        self.audio_out = None
        self.audio = None
        self.audio_rate = 16000
        self.audio_chunk = 320

    def _send(self, payload):
        try:
            self.sock.sendto(payload.encode(), (self.server_host, self.server_port))
        except Exception:
            pass

    def start(self):
        threading.Thread(target=self._listen, daemon=True).start()
        self._register()
        threading.Thread(target=self._heartbeat, daemon=True).start()
        threading.Thread(target=self._list_loop, daemon=True).start()

    def _listen(self):
        while self.running:
            try:
                data, _ = self.sock.recvfrom(65535)
            except Exception:
                continue
            msg = None
            try:
                msg = data.decode(errors="ignore").strip()
            except Exception:
                msg = None
            if not msg:
                continue
            if hasattr(self, "_last_msg") and self._last_msg == msg:
                try:
                    if time.time() - getattr(self, "_last_msg_time", 0) < 0.5:
                        continue
                except Exception:
                    pass
            self._last_msg = msg
            self._last_msg_time = time.time()
            if msg == "OK":
                self.connected = True
                self.status = "Conectado"
                if self.ui: self.ui.post(lambda: self.ui.set_status(self.status))
                if self.ui: self.ui.post(lambda: self.ui.log("[OK] ACK"))
            elif msg == "PONG":
                if self.status == "Sin conexión":
                    self.status = "Conectado"
                    if self.ui: self.ui.post(lambda: self.ui.set_status(self.status))
                if self.ui: self.ui.post(lambda: self.ui.log("[PONG]"))
                try:
                    self.last_pong = time.time()
                except Exception:
                    pass
            elif msg.startswith("LIST:"):
                payload = msg[5:]
                items = [x for x in payload.split(",") if x.strip()]
                if self.ui: self.ui.post(lambda: self.ui.set_online_count(len(items)))
                if self.ui: self.ui.post(lambda: self.ui.log(f"[LIST] {len(items)}"))
            elif msg.startswith("CALL_FROM:"):
                parts = msg.split(":")
                caller = parts[1] if len(parts) >= 2 else ""
                caller_name = parts[2] if len(parts) >= 3 else ""
                busy = False
                if self.peer:
                    busy = True
                if busy:
                    try:
                        self._send(f"BUSY:{caller}:{self.number}")
                    except Exception:
                        pass
                    if self.ui: self.ui.post(lambda: self.ui.log(f"[BUSY] a {caller}"))
                else:
                    self.peer = caller
                    if self.ui:
                        self.ui.post(lambda: self.ui.on_incoming_call(caller, caller_name))
                        self.ui.post(lambda: self.ui.start_ringtone())
            elif msg.startswith("ACCEPT_FROM:"):
                parts = msg.split(":")
                callee = parts[1] if len(parts) >= 2 else ""
                if self.ui: self.ui.post(lambda: self.ui.log(f"[ACCEPT_FROM] {callee}"))
                self.status = "En llamada"
                if self.ui: self.ui.post(lambda: self.ui.set_status(self.status))
                if self.ui: self.ui.post(lambda: self.ui.start_call_timer())
                if self.ui: self.ui.post(lambda: self.ui.stop_ringback())
                if self.ui: self.ui.post(lambda: self.ui.stop_ringtone())
                self.start_audio()
                self.call_pending = False
            elif msg.startswith("RINGING_FROM:"):
                callee = msg.split(":", 1)[1] if ":" in msg else ""
                if self.ui: self.ui.post(lambda: self.ui.log(f"[RINGING] {callee}"))
            elif msg.startswith("REJECT_FROM:"):
                if self.ui: self.ui.post(lambda: self.ui.log("[REJECT_FROM]"))
                self.peer = None
                self.status = "Listo"
                if self.ui: self.ui.post(lambda: self.ui.set_status(self.status))
                if self.ui: self.ui.post(lambda: self.ui.stop_ringback())
                if self.ui: self.ui.post(lambda: self.ui.stop_ringtone())
                self.stop_audio()
                self.call_pending = False
            elif msg.startswith("OFFLINE:"):
                callee = msg.split(":", 1)[1] if ":" in msg else ""
                self.peer = None
                self.status = f"No encontrado (OFFLINE: {callee})"
                if self.ui: self.ui.post(lambda: self.ui.set_status(self.status))
                if self.ui: self.ui.post(lambda: self.ui.log(f"[OFFLINE] destino {callee} no registrado"))
                if self.ui: self.ui.post(lambda: self.ui.stop_ringback())
                self.stop_audio()
                self.call_pending = False
            elif msg.startswith("ERR_NOUSER"):
                self.status = "No encontrado"
                self.peer = None
                if self.ui: self.ui.post(lambda: self.ui.set_status(self.status))
                if self.ui: self.ui.post(lambda: self.ui.log("[ERR_NOUSER] destino no registrado"))
                if self.ui: self.ui.post(lambda: self.ui.stop_ringback())
                self.stop_audio()
                self.call_pending = False
            elif msg.startswith("BUSY_FROM:"):
                if self.ui: self.ui.post(lambda: self.ui.log("[BUSY_FROM]"))
                self.peer = None
                self.status = "Listo"
                if self.ui: self.ui.post(lambda: self.ui.set_status(self.status))
                if self.ui: self.ui.post(lambda: self.ui.stop_ringback())
                self.stop_audio()
                self.call_pending = False
            elif msg.startswith("OFFER_FROM_B64:"):
                parts = msg.split(":")
                frm = parts[1]
                b64 = parts[2]
                try:
                    obj = json.loads(base64.b64decode(b64).decode())
                    if self.ui: self.ui.post(lambda: self.ui.log(f"[OFFER] de {frm} {obj.get('type')}"))
                except Exception:
                    if self.ui: self.ui.post(lambda: self.ui.log(f"[OFFER] error de parseo"))
            elif msg.startswith("ANSWER_FROM_B64:"):
                parts = msg.split(":")
                frm = parts[1]
                b64 = parts[2]
                try:
                    obj = json.loads(base64.b64decode(b64).decode())
                    if self.ui: self.ui.post(lambda: self.ui.log(f"[ANSWER] de {frm} {obj.get('type')}"))
                except Exception:
                    if self.ui: self.ui.post(lambda: self.ui.log(f"[ANSWER] error de parseo"))
                if self.ui: self.ui.post(lambda: self.ui.start_call_timer())
                self.status = "En llamada"
                if self.ui: self.ui.post(lambda: self.ui.set_status(self.status))
                if self.ui: self.ui.post(lambda: self.ui.stop_ringback())
                if self.ui: self.ui.post(lambda: self.ui.stop_ringtone())
                self.start_audio()
                self.call_pending = False
            elif msg.startswith("ICE_FROM_B64:"):
                parts = msg.split(":")
                frm = parts[1]
                if self.ui: self.ui.post(lambda: self.ui.log(f"[ICE] de {frm}"))
            elif msg.startswith("AUDIO_FROM_B64:"):
                parts = msg.split(":")
                b64 = parts[2] if len(parts) >= 3 else ""
                try:
                    data = base64.b64decode(b64)
                    if self.audio_out and (not self.ui or self.ui.speaker_on):
                        try:
                            self.audio_out.write(data)
                        except Exception:
                            pass
                except Exception:
                    pass
            elif msg.startswith("BYE_FROM:"):
                frm = msg.split(":")[1] if ":" in msg else ""
                if self.ui: self.ui.post(lambda: self.ui.log(f"[BYE] de {frm}"))
                self.peer = None
                self.status = "Listo"
                if self.ui: self.ui.post(lambda: self.ui.set_status(self.status))
                if self.ui: self.ui.post(lambda: self.ui.stop_call_timer())
                if self.ui: self.ui.post(lambda: self.ui.stop_ringtone())
                if self.ui: self.ui.post(lambda: self.ui.stop_ringback())
                self.stop_audio()
                self.call_pending = False

    def _register(self):
        self.status = "Conectando..."
        if self.ui: self.ui.post(lambda: self.ui.set_status(self.status))
        for _ in range(10):
            try:
                self._send(f"REGISTER:{self.number}:{self.local_port}:{self.name}")
            except Exception:
                pass
            time.sleep(0.5)
        if not self.connected:
            self.status = "Sin conexión"
            if self.ui: self.ui.post(lambda: self.ui.set_status(self.status))

    def _heartbeat(self):
        while self.running:
            try:
                self._send(f"PING:{self.number}")
            except Exception:
                pass
            time.sleep(10)
            try:
                if getattr(self, "last_pong", 0) and time.time() - self.last_pong > 25:
                    self.connected = False
                    self.status = "Sin conexión"
                    if self.ui: self.ui.post(lambda: self.ui.set_status(self.status))
                    self._register()
            except Exception:
                pass

    def _list_loop(self):
        while self.running:
            try:
                self._send("LIST")
            except Exception:
                pass
            time.sleep(20)

    def call(self, callee):
        if not callee:
            return
        self.peer = callee
        self.status = f"Llamando a {callee}..."
        if self.ui: self.ui.post(lambda: self.ui.set_status(self.status))
        self._send(f"CALL:{callee}:{self.number}")
        if self.ui: self.ui.post(lambda: self.ui.start_ringback())
        self.call_pending = True
        def timeout():
            time.sleep(30)
            try:
                if self.call_pending:
                    try:
                        self._send(f"BYE:{self.peer}:{self.number}")
                    except Exception:
                        pass
                    self.peer = None
                    self.status = "Sin respuesta"
                    if self.ui: self.ui.post(lambda: self.ui.set_status(self.status))
                    if self.ui: self.ui.post(lambda: self.ui.stop_ringback())
                else:
                    pass
            except Exception:
                pass
        try:
            threading.Thread(target=timeout, daemon=True).start()
        except Exception:
            pass

    def bye(self):
        if self.peer:
            self._send(f"BYE:{self.peer}:{self.number}")
        self.peer = None
        self.status = "Listo"
        if self.ui: self.ui.post(lambda: self.ui.set_status(self.status))
        self.stop_audio()

    def change_server(self, host, port):
        try:
            self.server_host = host
            self.server_port = int(port)
            self._register()
        except Exception:
            pass

    def accept(self, caller):
        self._send(f"ACCEPT:{caller}:{self.number}")
        self.status = "En llamada"
        if self.ui: self.ui.post(lambda: self.ui.set_status(self.status))
        if self.ui: self.ui.post(lambda: self.ui.start_call_timer())
        if self.ui: self.ui.post(lambda: self.ui.stop_ringtone())
        self.start_audio()

    def reject(self, caller):
        self._send(f"REJECT:{caller}:{self.number}")
        self.peer = None
        self.status = "Listo"
        if self.ui: self.ui.post(lambda: self.ui.set_status(self.status))

    def close(self):
        self.running = False
        try:
            self._send(f"UNREGISTER:{self.number}")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass

class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("VoIP Pro Windows")
        self.client = None
        self.fixed_host = "jacob.hidencloud.com"
        self.fixed_port = "24646"
        self.number_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.host_var = tk.StringVar(value=self.fixed_host)
        self.port_var = tk.StringVar(value=self.fixed_port)
        self.status_var = tk.StringVar(value="Listo")
        self.online_var = tk.StringVar(value="Conectados: 0")
        self.callee_var = tk.StringVar()
        self.server_label_var = tk.StringVar(value=f"Servidor: {self.fixed_host}:{self.fixed_port}")
        self.display_number_var = tk.StringVar(value="")
        self.display_name_var = tk.StringVar(value="")
        self.muted = False
        self.speaker_on = True
        self.call_seconds = 0
        self.call_timer_id = None
        self.keypad_buttons = []
        self.ringtone_running = False
        self.ringback_running = False
        self.ringtone_thread = None
        self.ringback_thread = None
        self.incoming_win = None
        self.sound_enabled = True
        self.partner_label_var = tk.StringVar(value="")
        self.audio_in_index = None
        self.audio_out_index = None
        self.audio_in_var = tk.StringVar()
        self.audio_out_var = tk.StringVar()
        self._build_ui()

    def _build_ui(self):
        self.root.configure(bg="#0A0A0F")
        title = tk.Label(self.root, textvariable=self.display_number_var, fg="#00FF88", bg="#0A0A0F", font=("Segoe UI", 24, "bold"))
        title.pack(pady=(16, 8))
        name_lbl = tk.Label(self.root, textvariable=self.display_name_var, fg="#00FF88", bg="#0A0A0F", font=("Segoe UI", 14))
        name_lbl.pack(pady=(0, 4))
        sub = tk.Label(self.root, textvariable=self.server_label_var, fg="#CCCCCC", bg="#0A0A0F", font=("Segoe UI", 10))
        sub.pack()
        actions_top = tk.Frame(self.root, bg="#0A0A0F")
        actions_top.pack(padx=16, pady=6, fill="x")
        tk.Button(actions_top, text="Ajustes", command=self.open_setup).pack(side="left")
        tk.Button(actions_top, text="Conectar", command=self.on_connect, bg="#00FF88").pack(side="left", padx=8)
        tk.Button(actions_top, text="Logs", command=self.open_logs).pack(side="left")
        top = tk.Frame(self.root, bg="#0A0A0F")
        top.pack(padx=16, pady=8, fill="x")
        tk.Label(top, textvariable=self.status_var, fg="#00FF88", bg="#0A0A0F", font=("Segoe UI", 12)).pack(side="left")
        tk.Label(top, textvariable=self.online_var, fg="#CCCCCC", bg="#0A0A0F", font=("Segoe UI", 12)).pack(side="right")
        display = tk.Entry(self.root, textvariable=self.callee_var, justify="center", font=("Segoe UI", 22, "bold"))
        display.pack(padx=16, pady=8, fill="x")
        keypad = tk.Frame(self.root, bg="#0A0A0F")
        keypad.pack(padx=16, pady=4)
        for row, digits in enumerate([["1","2","3"],["4","5","6"],["7","8","9"],["*","0","#"]]):
            r = tk.Frame(keypad, bg="#0A0A0F")
            r.pack()
            for d in digits:
                b = tk.Button(r, text=d, width=4, command=lambda x=d: self.append_digit(x))
                b.pack(side="left", padx=6, pady=6)
                self.keypad_buttons.append(b)
        ctl = tk.Frame(self.root, bg="#0A0A0F")
        ctl.pack(padx=16, pady=8)
        tk.Button(ctl, text="BACK", command=self.delete_digit).pack(side="left", padx=6)
        tk.Button(ctl, text="CLR", command=self.clear_number).pack(side="left", padx=6)
        actions = tk.Frame(self.root, bg="#0A0A0F")
        actions.pack(padx=16, pady=8)
        tk.Button(actions, text="LLAMAR", command=self.on_call, bg="#00FF88").pack(side="left", padx=8)
        tk.Button(actions, text="COLGAR", command=self.on_bye, bg="#FF3366", fg="#FFFFFF").pack(side="left", padx=8)
        callbar = tk.Frame(self.root, bg="#0A0A0F")
        callbar.pack(padx=16, pady=8)
        self.mic_btn = tk.Button(callbar, text="Mic ON", command=self.toggle_mute)
        self.mic_btn.pack(side="left", padx=6)
        self.spk_btn = tk.Button(callbar, text="Altavoz ON", command=self.toggle_speaker)
        self.spk_btn.pack(side="left", padx=6)
        self.sound_btn = tk.Button(callbar, text="Sonido ON", command=self.toggle_sound)
        self.sound_btn.pack(side="left", padx=6)
        self.dur_label = tk.Label(callbar, text="00:00", fg="#CCCCCC", bg="#0A0A0F")
        self.dur_label.pack(side="left", padx=6)
        tk.Label(callbar, text="Con:", fg="#CCCCCC", bg="#0A0A0F").pack(side="left", padx=(12,4))
        self.peer_label = tk.Label(callbar, textvariable=self.partner_label_var, fg="#00FF88", bg="#0A0A0F")
        self.peer_label.pack(side="left", padx=4)
        self.open_setup()

    def post(self, fn):
        self.root.after(0, fn)

    def set_status(self, s):
        self.status_var.set(s)

    def set_online_count(self, n):
        self.online_var.set(f"Conectados: {n}")

    def set_server_label(self, h, p):
        self.server_label_var.set(f"Servidor: {h}:{p}")

    def log(self, s):
        if not hasattr(self, "log_text") or self.log_text is None:
            self.open_logs()
        self.log_text.insert("end", s + "\n")
        self.log_text.see("end")

    def open_logs(self):
        if hasattr(self, "log_win") and self.log_win is not None:
            try:
                self.log_win.lift()
                return
            except Exception:
                self.log_win = None
        self.log_win = tk.Toplevel(self.root)
        self.log_win.title("Logs")
        self.log_text = tk.Text(self.log_win, height=20, width=80, bg="#111418", fg="#DDDDDD")
        self.log_text.pack(padx=12, pady=12, fill="both", expand=True)
        def on_close_logs():
            try:
                self.log_text = None
                self.log_win.destroy()
            except Exception:
                pass
            self.log_win = None
        self.log_win.protocol("WM_DELETE_WINDOW", on_close_logs)

    def on_incoming_call(self, caller, name):
        try:
            if self.incoming_win:
                self.incoming_win.destroy()
        except Exception:
            self.incoming_win = None
        self.incoming_win = tk.Toplevel(self.root)
        self.incoming_win.title("Llamada entrante")
        self.incoming_win.configure(bg="#0A0A0F")
        tk.Label(self.incoming_win, text=name or caller, fg="#00FF88", bg="#0A0A0F", font=("Segoe UI", 20, "bold")).pack(padx=16, pady=12)
        btns = tk.Frame(self.incoming_win, bg="#0A0A0F")
        btns.pack(padx=16, pady=12)
        self.set_partner(name or caller)
        def do_accept():
            try:
                self.stop_ringtone()
                if self.client: self.client.accept(caller)
            except Exception:
                pass
            try:
                self.incoming_win.destroy()
            except Exception:
                pass
            self.incoming_win = None
        def do_reject():
            try:
                self.stop_ringtone()
                if self.client: self.client.reject(caller)
            except Exception:
                pass
            try:
                self.incoming_win.destroy()
            except Exception:
                pass
            self.incoming_win = None
        tk.Button(btns, text="Aceptar", command=do_accept, bg="#00FF88", width=12).pack(side="left", padx=8)
        tk.Button(btns, text="Rechazar", command=do_reject, bg="#FF3366", fg="#FFFFFF", width=12).pack(side="left", padx=8)
        self.incoming_win.transient(self.root)
        self.incoming_win.grab_set()

    def on_connect(self):
        n = self.number_var.get().strip()
        nm = self.name_var.get().strip()
        h = self.fixed_host
        p = self.fixed_port
        if not n:
            self.open_setup()
            n = self.number_var.get().strip()
            nm = self.name_var.get().strip()
            if not n:
                return
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
        self.client = Client(h, p, n, nm, ui=self)
        self.client.start()
        self.log(f"Servidor: {h}:{p}")
        self.set_server_label(h, p)
        self.display_number_var.set(n)
        self.display_name_var.set(nm)

    def open_setup(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Ajustes")
        dlg.configure(bg="#0A0A0F")
        tk.Label(dlg, text="Número", fg="#00FF88", bg="#0A0A0F").grid(row=0, column=0, sticky="w", padx=12, pady=6)
        ent_num = tk.Entry(dlg, textvariable=self.number_var)
        ent_num.grid(row=0, column=1, sticky="ew", padx=12, pady=6)
        tk.Label(dlg, text="Nombre", fg="#00FF88", bg="#0A0A0F").grid(row=1, column=0, sticky="w", padx=12, pady=6)
        ent_name = tk.Entry(dlg, textvariable=self.name_var)
        ent_name.grid(row=1, column=1, sticky="ew", padx=12, pady=6)
        rowi = 2
        if pyaudio:
            tk.Label(dlg, text="Micrófono", fg="#00FF88", bg="#0A0A0F").grid(row=rowi, column=0, sticky="w", padx=12, pady=6)
            in_opts = []
            out_opts = []
            try:
                pa = pyaudio.PyAudio()
                ndev = pa.get_device_count()
                for i in range(ndev):
                    info = pa.get_device_info_by_index(i)
                    if info.get("maxInputChannels", 0) > 0:
                        in_opts.append(f"{i}:{info.get('name','')}")
                    if info.get("maxOutputChannels", 0) > 0:
                        out_opts.append(f"{i}:{info.get('name','')}")
                try:
                    di = pa.get_default_input_device_info()
                    if di:
                        self.audio_in_var.set(f"{di.get('index')}:{di.get('name','')}")
                        self.audio_in_index = di.get('index')
                except Exception:
                    pass
                try:
                    do = pa.get_default_output_device_info()
                    if do:
                        self.audio_out_var.set(f"{do.get('index')}:{do.get('name','')}")
                        self.audio_out_index = do.get('index')
                except Exception:
                    pass
                try:
                    pa.terminate()
                except Exception:
                    pass
            except Exception:
                in_opts = []
                out_opts = []
            if not in_opts:
                in_opts = ["-1:Default"]
                if not self.audio_in_var.get():
                    self.audio_in_var.set(in_opts[0])
            if not out_opts:
                out_opts = ["-1:Default"]
                if not self.audio_out_var.get():
                    self.audio_out_var.set(out_opts[0])
            in_menu = tk.OptionMenu(dlg, self.audio_in_var, *in_opts)
            in_menu.grid(row=rowi, column=1, sticky="ew", padx=12, pady=6)
            rowi += 1
            tk.Label(dlg, text="Altavoz", fg="#00FF88", bg="#0A0A0F").grid(row=rowi, column=0, sticky="w", padx=12, pady=6)
            out_menu = tk.OptionMenu(dlg, self.audio_out_var, *out_opts)
            out_menu.grid(row=rowi, column=1, sticky="ew", padx=12, pady=6)
            rowi += 1
        btns = tk.Frame(dlg, bg="#0A0A0F")
        btns.grid(row=rowi, column=0, columnspan=2, sticky="ew", padx=12, pady=6)
        def ok():
            self.display_number_var.set(self.number_var.get().strip())
            self.display_name_var.set(self.name_var.get().strip())
            try:
                val = self.audio_in_var.get()
                if val:
                    ix = int(val.split(":")[0])
                    self.audio_in_index = None if ix < 0 else ix
            except Exception:
                pass
            try:
                val = self.audio_out_var.get()
                if val:
                    ix = int(val.split(":")[0])
                    self.audio_out_index = None if ix < 0 else ix
            except Exception:
                pass
            dlg.destroy()
        def cancel():
            dlg.destroy()
        tk.Button(btns, text="OK", command=ok, bg="#00FF88").pack(side="left", padx=6)
        tk.Button(btns, text="Cancelar", command=cancel).pack(side="left", padx=6)
        dlg.columnconfigure(1, weight=1)
        dlg.transient(self.root)
        dlg.grab_set()
        self.root.wait_window(dlg)

    def on_call(self):
        if not self.client:
            return
        callee = self.callee_var.get().strip()
        self.set_partner(callee)
        self.client.call(callee)

    def on_bye(self):
        if self.client:
            self.client.bye()

    def append_digit(self, d):
        cur = self.callee_var.get()
        self.callee_var.set(cur + d)

    def delete_digit(self):
        cur = self.callee_var.get()
        if cur:
            self.callee_var.set(cur[:-1])

    def clear_number(self):
        self.callee_var.set("")

    def toggle_mute(self):
        self.muted = not self.muted
        self.mic_btn.configure(text="Mic OFF" if self.muted else "Mic ON")

    def toggle_speaker(self):
        self.speaker_on = not self.speaker_on
        self.spk_btn.configure(text="Altavoz ON" if self.speaker_on else "Altavoz OFF")

    def start_call_timer(self):
        self.stop_call_timer()
        self.call_seconds = 0
        def tick():
            self.call_seconds += 1
            m = self.call_seconds // 60
            s = self.call_seconds % 60
            self.dur_label.configure(text=f"{m:02d}:{s:02d}")
            self.call_timer_id = self.root.after(1000, tick)
        self.call_timer_id = self.root.after(1000, tick)
        for b in self.keypad_buttons:
            try:
                b.configure(state="disabled")
            except Exception:
                pass

    def stop_call_timer(self):
        if self.call_timer_id:
            self.root.after_cancel(self.call_timer_id)
            self.call_timer_id = None
        self.call_seconds = 0
        self.dur_label.configure(text="00:00")
        for b in self.keypad_buttons:
            try:
                b.configure(state="normal")
            except Exception:
                pass
        self.clear_partner()

    def start_ringtone(self):
        if not self.sound_enabled:
            return
        if self.ringtone_running:
            return
        self.ringtone_running = True
        def loop():
            while self.ringtone_running:
                if not self.sound_enabled:
                    time.sleep(0.2)
                    continue
                try:
                    winsound.Beep(1200, 200)
                    time.sleep(0.2)
                    winsound.Beep(1200, 200)
                    time.sleep(1.2)
                except Exception:
                    time.sleep(1)
        try:
            self.ringtone_thread = threading.Thread(target=loop, daemon=True)
            self.ringtone_thread.start()
        except Exception:
            self.ringtone_running = False

    def stop_ringtone(self):
        self.ringtone_running = False

    def start_ringback(self):
        if not self.sound_enabled:
            return
        if self.ringback_running:
            return
        self.ringback_running = True
        def loop():
            while self.ringback_running:
                if not self.sound_enabled:
                    time.sleep(0.2)
                    continue
                try:
                    winsound.Beep(440, 400)
                    time.sleep(0.2)
                    winsound.Beep(440, 400)
                    time.sleep(2.0)
                except Exception:
                    time.sleep(1)
        try:
            self.ringback_thread = threading.Thread(target=loop, daemon=True)
            self.ringback_thread.start()
        except Exception:
            self.ringback_running = False

    def stop_ringback(self):
        self.ringback_running = False

    def toggle_sound(self):
        self.sound_enabled = not self.sound_enabled
        try:
            self.sound_btn.configure(text="Sonido ON" if self.sound_enabled else "Sonido OFF")
        except Exception:
            pass
        if not self.sound_enabled:
            try:
                self.stop_ringtone()
                self.stop_ringback()
            except Exception:
                pass
        else:
            try:
                if self.client and self.client.peer:
                    pass
            except Exception:
                pass
        self.audio_enabled = self.sound_enabled

    def set_partner(self, who):
        self.partner_label_var.set(who or "")

    def clear_partner(self):
        self.partner_label_var.set("")

    def start_audio(self):
        if not self.audio_enabled:
            return
        if not pyaudio:
            if self.ui: self.ui.post(lambda: self.ui.log("[AUDIO] no disponible, instale pyaudio"))
            return
        try:
            if not self.audio:
                self.audio = pyaudio.PyAudio()
            if not self.audio_in:
                kw = dict(format=pyaudio.paInt16, channels=1, rate=self.audio_rate, input=True, frames_per_buffer=self.audio_chunk)
                if self.ui and self.ui.audio_in_index is not None:
                    kw["input_device_index"] = self.ui.audio_in_index
                try:
                    self.audio_in = self.audio.open(**kw)
                except Exception:
                    try:
                        self.audio_rate = 16000
                        kw["rate"] = self.audio_rate
                        self.audio_in = self.audio.open(**kw)
                    except Exception:
                        raise
            if not self.audio_out:
                kw = dict(format=pyaudio.paInt16, channels=1, rate=self.audio_rate, output=True, frames_per_buffer=self.audio_chunk)
                if self.ui and self.ui.audio_out_index is not None:
                    kw["output_device_index"] = self.ui.audio_out_index
                try:
                    self.audio_out = self.audio.open(**kw)
                except Exception:
                    try:
                        self.audio_rate = 16000
                        kw["rate"] = self.audio_rate
                        self.audio_out = self.audio.open(**kw)
                    except Exception:
                        raise
        except Exception:
            try:
                self.audio_running = False
                if self.ui: self.ui.post(lambda: self.ui.log("[AUDIO] error al abrir dispositivos"))
            except Exception:
                pass
            return
        if self.audio_running:
            return
        self.audio_running = True
        def loop():
            while self.audio_running and self.peer:
                try:
                    if self.ui and self.ui.muted:
                        time.sleep(0.02)
                        continue
                    buf = self.audio_in.read(self.audio_chunk, exception_on_overflow=False)
                    b64 = base64.b64encode(buf).decode()
                    self._send(f"AUDIO_B64:{self.peer}:{self.number}:{b64}")
                except Exception:
                    time.sleep(0.01)
        try:
            threading.Thread(target=loop, daemon=True).start()
        except Exception:
            self.audio_running = False

    def stop_audio(self):
        self.audio_running = False
        try:
            if self.audio_in:
                try:
                    self.audio_in.stop_stream()
                    self.audio_in.close()
                except Exception:
                    pass
                self.audio_in = None
            if self.audio_out:
                try:
                    self.audio_out.stop_stream()
                    self.audio_out.close()
                except Exception:
                    pass
                self.audio_out = None
            if self.audio:
                try:
                    self.audio.terminate()
                except Exception:
                    pass
                self.audio = None
        except Exception:
            pass

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.mainloop()

    def on_close(self):
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
        self.root.destroy()

def main():
    app = App()
    app.run()

if __name__ == "__main__":
    main()
