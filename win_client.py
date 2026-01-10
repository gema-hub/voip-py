import socket
import threading
import time
import base64
import logging
import tkinter as tk
from tkinter import ttk, messagebox
import sys
import random
import array
from queue import Queue

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
        
        # Configuración de Audio Dispositivos
        self.input_device_index = -1
        self.output_device_index = -1
        
        # Configuración de Procesamiento de Audio
        self.input_gain = 1.0 
        self.isolation_enabled = False 
        self.noise_gate_threshold = 500 
        
        # Configuración Física
        self.audio_rate = 16000
        self.audio_chunk = 320
        self.audio_format = 8 
        self.audio_channels = 1
        
        # Buffer de reproducción (Jitter Buffer)
        self.audio_queue = Queue(maxsize=40) 
        
        if PYAUDIO_AVAILABLE:
            self.audio_format = pyaudio.paInt16
        
        self.p = None
        self.stream_in = None
        self.stream_out = None
        self.playback_thread = None 
        
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
                data, _ = self.sock.recvfrom(4096)
                msg = data.decode(errors="ignore").strip()
                if msg:
                    self._process_message(msg)
            except socket.error:
                if self.running:
                    time.sleep(0.1)
            except Exception as e:
                logger.error(f"Error en listen loop: {e}")

    def _process_message(self, msg):
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
            parts = msg.split(":", 2)
            if len(parts) >= 3:
                caller = parts[1]
                caller_name = parts[2]
                self._handle_incoming_call(caller, caller_name)

        elif msg.startswith("ACCEPT_FROM:"):
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
            pass 

        elif msg.startswith("AUDIO_FROM_B64:"):
            parts = msg.split(":", 2)
            if len(parts) == 3:
                b64_data = parts[2]
                self._enqueue_audio(b64_data)

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

    # --- Audio Mejorado con Procesamiento ---

    def _init_audio(self):
        if not PYAUDIO_AVAILABLE:
            return
        
        try:
            if self.p:
                self.p.terminate()
            self.p = pyaudio.PyAudio()
        except:
            self.p = pyaudio.PyAudio()
        
        try:
            input_kwargs = {
                'format': self.audio_format,
                'channels': self.audio_channels,
                'rate': self.audio_rate,
                'input': True,
                'frames_per_buffer': self.audio_chunk,
                'stream_callback': self._audio_input_callback
            }
            if self.input_device_index >= 0:
                input_kwargs['input_device_index'] = self.input_device_index
                
            self.stream_in = self.p.open(**input_kwargs)
            
            output_kwargs = {
                'format': self.audio_format,
                'channels': self.audio_channels,
                'rate': self.audio_rate,
                'output': True,
                'frames_per_buffer': self.audio_chunk
            }
            if self.output_device_index >= 0:
                output_kwargs['output_device_index'] = self.output_device_index
            
            self.stream_out = self.p.open(**output_kwargs)
            
            self.stream_in.start_stream()
            
            self.playback_thread = threading.Thread(target=self._audio_playback_worker, daemon=True)
            self.playback_thread.start()
            
            logger.info(f"Audio streams iniciados. In: {self.input_device_index}, Out: {self.output_device_index}")
            
        except Exception as e:
            logger.error(f"Error iniciando audio: {e}")
            self.ui.update_status("Error Audio")

    def _enqueue_audio(self, b64_data):
        if not self.in_call or not self.ui.speaker_on:
            return
        try:
            data = base64.b64decode(b64_data)
            if self.audio_queue.full():
                try:
                    self.audio_queue.get_nowait()
                except:
                    pass
            self.audio_queue.put_nowait(data)
        except Exception:
            pass

    def _audio_playback_worker(self):
        silence = b'\x00' * (self.audio_chunk * 2) 
        
        while self.in_call and self.running:
            try:
                chunk = self.audio_queue.get(timeout=0.02)
                if self.stream_out:
                    try:
                        self.stream_out.write(chunk)
                    except OSError:
                        pass
                    
            except Exception:
                if self.stream_out:
                    try:
                        self.stream_out.write(silence)
                    except OSError:
                        pass

    def _audio_input_callback(self, in_data, frame_count, time_info, status):
        if self.in_call and not self.ui.muted:
            try:
                data_array = array.array('h', in_data)
                
                if self.isolation_enabled:
                    volume = sum(abs(x) for x in data_array) / len(data_array)
                    if volume < self.noise_gate_threshold:
                        return (None, pyaudio.paContinue)

                if self.input_gain != 1.0:
                    for i in range(len(data_array)):
                        val = int(data_array[i] * self.input_gain)
                        if val >32767: val = 32767
                        if val < -32768: val = -32768
                        data_array[i] = val
                
                processed_data = data_array.tobytes()
                
                b64 = base64.b64encode(processed_data).decode()
                self.send(f"AUDIO_B64:{self.peer}:{self.number}:{b64}")
                
            except Exception as e:
                logger.error(f"Error procesando audio input: {e}")
                
        return (None, pyaudio.paContinue)

    def _stop_audio(self):
        self.in_call = False 
        
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
            
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except:
                pass

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

# ================= UI =================
class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("VoIP Pro Settings")
        self.root.geometry("360x700")
        self.root.resizable(False, False)
        
        self.colors = {
            "bg": "#0F1014", "surface": "#1C1E26", "text": "#E0E6ED",
            "text_muted": "#8B9BB4", "primary": "#00E676", "danger": "#FF3B30", "icon": "#FFFFFF"
        }
        self.root.configure(bg=self.colors["bg"])
        
        self.client = None
        self.muted = False
        self.speaker_on = True
        self.ringtone_active = False
        self.ringback_active = False
        
        random_suffix = "".join([str(random.randint(0, 9)) for _ in range(7)])
        self.default_number = "098" + random_suffix
        
        self._build_ui()
        self.server_host = "jacob.hidencloud.com"
        self.server_port = "24646"

    def _build_ui(self):
        header = tk.Frame(self.root, bg=self.colors["bg"], height=50)
        header.pack(fill="x")
        self.status_dot = tk.Label(header, text="●", fg=self.colors["danger"], bg=self.colors["bg"], font=("Segoe UI", 12))
        self.status_dot.pack(side="left", padx=15, pady=10)
        self.status_lbl = tk.Label(header, text="Desconectado", fg=self.colors["text_muted"], bg=self.colors["bg"], font=("Segoe UI", 12, "bold"))
        self.status_lbl.pack(side="left")
        self.settings_btn = tk.Button(header, text="⚙️", bg=self.colors["bg"], fg=self.colors["text"], font=("Segoe UI", 14), bd=0, command=self._open_settings)
        self.settings_btn.pack(side="right", padx=15)

        display_frame = tk.Frame(self.root, bg=self.colors["bg"])
        display_frame.pack(expand=True, fill="x", padx=20)
        self.number_var = tk.StringVar()
        self.display_entry = tk.Label(display_frame, textvariable=self.number_var, fg=self.colors["text"], bg=self.colors["bg"], font=("Segoe UI", 28, "bold"), anchor="center")
        self.display_entry.pack(pady=20)
        self.my_info_lbl = tk.Label(display_frame, text=f"N/D", fg=self.colors["text_muted"], bg=self.colors["bg"], font=("Segoe UI", 10))
        self.my_info_lbl.pack()

        keypad_frame = tk.Frame(self.root, bg=self.colors["bg"], padx=20)
        keypad_frame.pack(fill="x", pady=10)
        keys = [[("1", " "), ("2", "ABC"), ("3", "DEF")],[("4", "GHI"), ("5", "JKL"), ("6", "MNO")],[("7", "PQRS"), ("8", "TUV"), ("9", "WXYZ")],[("*", ""), ("0", "+"), ("#", "")]]
        for row in keys:
            r_frame = tk.Frame(keypad_frame, bg=self.colors["bg"])
            r_frame.pack(pady=5)
            for num, sub in row:
                btn = tk.Button(r_frame, text=num, font=("Segoe UI", 22, "bold"), fg=self.colors["text"], bg=self.colors["surface"], activebackground=self.colors["bg"], activeforeground=self.colors["primary"], width=4, height=1, bd=0, cursor="hand2", command=lambda x=num: self._append(x))
                btn.pack(side="left", padx=10)

        action_frame = tk.Frame(self.root, bg=self.colors["bg"], height=100)
        action_frame.pack(fill="x", padx=20, pady=20)
        self.back_btn = tk.Button(action_frame, text="⌫", font=("Segoe UI", 24), fg=self.colors["text"], bg=self.colors["bg"], bd=0, command=self._backspace)
        self.back_btn.pack(side="left", padx=10)
        self.call_btn = tk.Button(action_frame, text="📞", font=("Segoe UI", 28), fg=self.colors["bg"], bg=self.colors["primary"], activebackground="#00C853", width=6, height=2, bd=0, cursor="hand2", relief="flat", command=self._on_call)
        self.call_btn.pack(side="left", expand=True, padx=20)
        self.audio_ctrl_frame = tk.Frame(self.root, bg=self.colors["bg"], height=50)
        self.mute_btn = tk.Button(self.audio_ctrl_frame, text="🎤", bg=self.colors["surface"], fg=self.colors["text"], bd=0, command=self._toggle_mute)
        self.spk_btn = tk.Button(self.audio_ctrl_frame, text="🔊", bg=self.colors["surface"], fg=self.colors["text"], bd=0, command=self._toggle_speaker)
        self.hangup_btn = tk.Button(action_frame, text="", width=6, height=2, command=self._on_hangup)

    def _append(self, char): self.number_var.set(self.number_var.get() + char)
    def _backspace(self): self.number_var.set(self.number_var.get()[:-1])
    def _on_call(self):
        if self.client: self.client.call(self.number_var.get().strip())
    def _on_hangup(self):
        if self.client: self.client.hangup()
    def _toggle_mute(self):
        self.muted = not self.muted
        color = self.colors["danger"] if self.muted else self.colors["text"]
        self.mute_btn.configure(fg=color)
    def _toggle_speaker(self):
        self.speaker_on = not self.speaker_on
        color = self.colors["danger"] if not self.speaker_on else self.colors["text"]
        self.spk_btn.configure(fg=color)

    def update_status(self, text):
        def _update():
            self.status_lbl.configure(text=text)
            if "Conectado" in text:
                self.status_dot.configure(fg=self.colors["primary"])
                if self.client: self.my_info_lbl.configure(text=f"{self.client.name or 'User'} | {self.client.number}")
            else:
                self.status_dot.configure(fg=self.colors["danger"])
                if self.client: self.my_info_lbl.configure(text=f"{self.client.number}")
        self.root.after(0, _update)

    def update_online_count(self, n): pass
    def log(self, msg): pass 
    def set_in_call_ui(self, in_call):
        def _update():
            if in_call:
                self.display_entry.configure(fg=self.colors["primary"])
                self.call_btn.place_forget()
                self.back_btn.place_forget()
                self.hangup_btn.config(text="🛑", fg="white", bg=self.colors["danger"], font=("Segoe UI", 28), relief="flat", bd=0)
                self.hangup_btn.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.4, relheight=0.6)
                self.audio_ctrl_frame.pack(side="bottom", fill="x", padx=20, pady=10)
                self.mute_btn.pack(side="left", padx=10, expand=True, fill="x")
                self.spk_btn.pack(side="left", padx=10, expand=True, fill="x")
            else:
                self.display_entry.configure(fg=self.colors["text"])
                self.hangup_btn.place_forget()
                self.audio_ctrl_frame.pack_forget()
                self.back_btn.pack(side="left", padx=10)
                self.call_btn.pack(side="left", expand=True, padx=20)
        self.root.after(0, _update)

    def start_call_timer(self):
        self.seconds = 0
        def tick():
            if self.client and self.client.in_call:
                self.seconds += 1
                m, s = divmod(self.seconds, 60)
                self.display_entry.configure(text=f"{m:02d}:{s:02d}")
                self.root.after(1000, tick)
            else:
                if self.client and self.client.peer: self.display_entry.configure(text=self.client.peer)
        self.root.after(0, tick)
    def stop_call_timer(self): pass

    # --- AJUSTES CORREGIDOS ---
    def _open_settings(self):
        if not PYAUDIO_AVAILABLE:
            messagebox.showerror("Error", "PyAudio no está instalado. No se pueden ajustar dispositivos.")
            return

        win = tk.Toplevel(self.root)
        win.title("Configuración")
        win.geometry("520x640")
        win.configure(bg=self.colors["bg"])
        win.transient(self.root)
        win.grab_set()

        canvas = tk.Canvas(win, bg=self.colors["bg"], highlightthickness=0)
        scrollbar = tk.Scrollbar(win, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg=self.colors["bg"])

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        card1 = tk.Frame(scrollable_frame, bg=self.colors["surface"], padx=20, pady=20)
        card1.pack(fill="x", padx=15, pady=(20, 10))
        tk.Label(card1, text="👤 PERFIL & CUENTA", fg=self.colors["primary"], bg=self.colors["surface"], font=("Segoe UI", 14, "bold")).pack(anchor="w")
        tk.Label(card1, text="Configura tu identidad en la red.", fg=self.colors["text_muted"], bg=self.colors["surface"], font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 15))
        current_num = self.default_number
        current_name = "User"
        if self.client:
            current_num = self.client.number
            current_name = self.client.name
        tk.Label(card1, text="Tu Número:", fg=self.colors["text"], bg=self.colors["surface"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
        num_var = tk.StringVar(value=current_num)
        e1 = tk.Entry(card1, textvariable=num_var, bg=self.colors["bg"], fg=self.colors["text"], bd=0, insertbackground=self.colors["primary"], font=("Segoe UI", 12))
        e1.pack(fill="x", ipady=8, pady=(5, 15))
        tk.Label(card1, text="Nombre Visible:", fg=self.colors["text"], bg=self.colors["surface"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
        name_var = tk.StringVar(value=current_name)
        e2 = tk.Entry(card1, textvariable=name_var, bg=self.colors["bg"], fg=self.colors["text"], bd=0, insertbackground=self.colors["primary"], font=("Segoe UI", 12))
        e2.pack(fill="x", ipady=8)

        card2 = tk.Frame(scrollable_frame, bg=self.colors["surface"], padx=20, pady=20)
        card2.pack(fill="x", padx=15, pady=10)
        tk.Label(card2, text="🎧 DISPOSITIVOS", fg=self.colors["primary"], bg=self.colors["surface"], font=("Segoe UI", 14, "bold")).pack(anchor="w")
        tk.Label(card2, text="Selecciona qué hardware usarás.", fg=self.colors["text_muted"], bg=self.colors["surface"], font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 15))

        p = pyaudio.PyAudio()
        input_devices = ["Predeterminado del Sistema"]
        output_devices = ["Predeterminado del Sistema"]
        
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            max_in = info.get('maxInputChannels', 0)
            if isinstance(max_in, (int, float)) and max_in > 0:
                # CORRECCIÓN: Forzar a string para evitar errores de tipo
                name_dev = str(info.get('name', 'Unknown'))
                if len(name_dev) > 30: name_dev = name_dev[:27] + "..."
                input_devices.append(f"{i}: {name_dev}")
            
            max_out = info.get('maxOutputChannels', 0)
            if isinstance(max_out, (int, float)) and max_out > 0:
                # CORRECCIÓN: Forzar a string para evitar errores de tipo
                name_dev = str(info.get('name', 'Unknown'))
                if len(name_dev) > 30: name_dev = name_dev[:27] + "..."
                output_devices.append(f"{i}: {name_dev}")
        p.terminate()

        tk.Label(card2, text="Micrófono Entrada:", fg=self.colors["text"], bg=self.colors["surface"], font=("Segoe UI", 11)).pack(anchor="w")
        in_dev_var = tk.StringVar()
        in_combo = ttk.Combobox(card2, textvariable=in_dev_var, values=input_devices, state="readonly")
        in_combo.set("Predeterminado del Sistema")
        if self.client and self.client.input_device_index >= 0:
            for d in input_devices:
                if d.startswith(f"{self.client.input_device_index}:"):
                    in_combo.set(d)
                    break
        in_combo.pack(fill="x", pady=(5, 15))

        tk.Label(card2, text="Altavoz Salida:", fg=self.colors["text"], bg=self.colors["surface"], font=("Segoe UI", 11)).pack(anchor="w")
        out_dev_var = tk.StringVar()
        out_combo = ttk.Combobox(card2, textvariable=out_dev_var, values=output_devices, state="readonly")
        out_combo.set("Predeterminado del Sistema")
        if self.client and self.client.output_device_index >= 0:
            for d in output_devices:
                if d.startswith(f"{self.client.output_device_index}:"):
                    out_combo.set(d)
                    break
        out_combo.pack(fill="x", pady=5)

        card3 = tk.Frame(scrollable_frame, bg=self.colors["surface"], padx=20, pady=20)
        card3.pack(fill="x", padx=15, pady=10)
        tk.Label(card3, text="⚙️ FILTROS Y CALIDAD", fg=self.colors["primary"], bg=self.colors["surface"], font=("Segoe UI", 14, "bold")).pack(anchor="w")
        tk.Label(card3, text="Ajusta cómo escucha tu micrófono.", fg=self.colors["text_muted"], bg=self.colors["surface"], font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 15))
        
        iso_var = tk.BooleanVar(value=self.client.isolation_enabled if self.client else False)
        chk = tk.Checkbutton(card3, text="Aislamiento de Voz (Noise Gate)", variable=iso_var, bg=self.colors["surface"], fg=self.colors["text"], selectcolor=self.colors["bg"], activebackground=self.colors["surface"], activeforeground=self.colors["primary"], cursor="hand2", font=("Segoe UI", 11))
        chk.pack(anchor="w", pady=(0, 20))
        
        tk.Label(card3, text="Potencia / Ganancia (Gain):", fg=self.colors["text"], bg=self.colors["surface"], font=("Segoe UI", 11)).pack(anchor="w")
        gain_var = tk.DoubleVar(value=self.client.input_gain if self.client else 1.0)
        slider_frame = tk.Frame(card3, bg=self.colors["surface"])
        slider_frame.pack(fill="x")
        gain_scale = tk.Scale(slider_frame, from_=0.5, to=3.0, resolution=0.1, orient="horizontal", variable=gain_var, bg=self.colors["surface"], fg=self.colors["text"], highlightthickness=0, troughcolor=self.colors["bg"], cursor="hand2")
        gain_scale.pack(side="left", fill="x", expand=True)
        tk.Label(slider_frame, text="NORMAL", fg=self.colors["text_muted"], bg=self.colors["surface"], font=("Segoe UI", 8)).pack(side="right", padx=(5,0))

        btn_card = tk.Frame(scrollable_frame, bg=self.colors["bg"], padx=15, pady=20)
        btn_card.pack(fill="x", side="bottom")
        
        def save():
            n = num_var.get().strip()
            nm = name_var.get().strip()
            
            in_idx = -1
            if "Predeterminado" not in in_dev_var.get():
                try:
                    in_idx = int(in_dev_var.get().split(":")[0])
                except: pass
            
            out_idx = -1
            if "Predeterminado" not in out_dev_var.get():
                try:
                    out_idx = int(out_dev_var.get().split(":")[0])
                except: pass
            
            if n:
                self._connect(n, nm, in_idx, out_idx, gain_var.get(), iso_var.get())
                win.destroy()
        
        tk.Button(btn_card, text="GUARDAR CAMBIOS", bg=self.colors["primary"], fg="black", font=("Segoe UI", 11, "bold"), bd=0, height=2, cursor="hand2", command=save).pack(fill="x", ipady=5)

    def _connect(self, number, name, in_idx, out_idx, gain, isolation):
        if self.client: self.client.close()
        self.update_status("Conectando...")
        self.client = VoIPClient(self.server_host, self.server_port, number, name, self)
        self.client.input_device_index = in_idx
        self.client.output_device_index = out_idx
        self.client.input_gain = gain
        self.client.isolation_enabled = isolation
        self.client._register()

    def on_incoming_call(self, caller, name):
        if hasattr(self, 'incoming_win') and self.incoming_win and self.incoming_win.winfo_exists(): return
        self.start_ringtone()
        self.incoming_win = tk.Toplevel(self.root)
        self.incoming_win.title("Llamada")
        self.incoming_win.geometry("360x700")
        self.incoming_win.configure(bg=self.colors["bg"])
        self.incoming_win.attributes('-topmost', True)
        self.incoming_win.overrideredirect(True)
        avatar = tk.Label(self.incoming_win, text="📞", font=("Segoe UI", 64), bg=self.colors["surface"], fg=self.colors["primary"], width=8, height=4)
        avatar.place(relx=0.5, rely=0.3, anchor="center")
        tk.Label(self.incoming_win, text=f"LLAMADA ENTRANTE", fg=self.colors["text_muted"], bg=self.colors["bg"], font=("Segoe UI", 12)).place(relx=0.5, rely=0.55, anchor="center")
        tk.Label(self.incoming_win, text=name or caller, fg=self.colors["text"], bg=self.colors["bg"], font=("Segoe UI", 24, "bold")).place(relx=0.5, rely=0.62, anchor="center")
        tk.Label(self.incoming_win, text=caller, fg=self.colors["text_muted"], bg=self.colors["bg"], font=("Segoe UI", 16)).place(relx=0.5, rely=0.68, anchor="center")
        btns = tk.Frame(self.incoming_win, bg=self.colors["bg"])
        btns.place(relx=0.5, rely=0.85, anchor="center", width=300, height=80)
        def acc():
            self.stop_ringtone()
            if self.client: self.client.accept(caller)
            self.incoming_win.destroy()
        def rej():
            self.stop_ringtone()
            if self.client: self.client.reject(caller)
            self.incoming_win.destroy()
        tk.Button(btns, text="🛑", bg=self.colors["danger"], fg="white", font=("Segoe UI", 20), width=8, height=2, bd=0, command=rej).pack(side="left", padx=20)
        tk.Button(btns, text="📞", bg=self.colors["primary"], fg="black", font=("Segoe UI", 20), width=8, height=2, bd=0, command=acc).pack(side="right", padx=20)

    def start_ringtone(self):
        if self.ringtone_active or not WINSOUND_AVAILABLE: return
        self.ringtone_active = True
        threading.Thread(target=self._play_ringtone_loop, daemon=True).start()

    def stop_ringtone(self): self.ringtone_active = False
    
    def start_ringback(self):
        if self.ringback_active or not WINSOUND_AVAILABLE: return
        self.ringback_active = True
        threading.Thread(target=self._play_ringback_loop, daemon=True).start()
    def stop_ringback(self): self.ringback_active = False

    def _play_ringtone_loop(self):
        while self.ringtone_active:
            try: winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC); time.sleep(1.5)
            except: break

    def _play_ringback_loop(self):
        while self.ringback_active:
            try: winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC); time.sleep(2.0)
            except: break

    def _on_exit(self):
        if self.client: self.client.close()
        self.root.destroy()
        sys.exit(0)

    def run(self): self.root.mainloop()

if __name__ == "__main__":
    app = App()
    app.run()
