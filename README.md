# 📞 VOIP-PY — Llamadas P2P desde PC

> Aplicación VoIP hecha en Python para hacer **llamadas de voz P2P** entre equipos, sin servidores centrales.  
> No busco estrellas, busco **comentarios, issues y PRs** con feedback técnico.

---

## 🎯 Descripción

VOIP-PY permite que dos PCs se comuniquen por audio en tiempo real usando sockets (UDP/TCP), compresión de audio y descubrimiento entre pares.

La idea es hacer llamadas “normales” entre equipos sin pasar por proveedores externos.

---

## 🧩 Objetivos del Proyecto

- Comunicación **P2P** directa
- **Baja latencia** y audio fluido
- Compatible Windows / Linux
- Sin dependencias de servicios externos
- Opción futura de **relay** si NAT complica

Si tienes experiencia en:
- RTP/WebRTC
- Audio realtime
- NAT traversal (STUN/TURN/ICE)
- Compresión Opus
- Packet jitter / buffering

👉 Deja feedback técnico en un Issue.

---

## 🔧 Tecnologías Usadas

- Python ≥ 3.12
- `pyaudio` o `sounddevice` (captura & playback)
- `socket` (UDP/TCP)
- `threading` o `asyncio`
- Opcional: `opuslib` para compresión

---

## 🗂 Características

- Captura de micrófono
- Envío de audio en tiempo real
- Buffer anti-jitter básico
- Modo cliente/servidor P2P
- Poca latencia (depende red)

---

## 🚧 Próximas Mejoras (TODO)

- [ ] Compresión **Opus**
- [ ] Anti-jitter avanzado
- [ ] Detección NAT + STUN
- [ ] Relay opcional para NAT estrictos
- [ ] GUI mínima (Tk/Qt/Web)
- [ ] Modo conferencia
- [ ] Cifrado (AES/DTLS)
- [ ] Benchmarks de latencia
- [ ] Compilación con Nuitka (binario)

---

## 📦 Compilación Nuitka (opcional)

```bash
python -m nuitka --standalone --onefile --remove-output \
    --enable-console \
    --follow-imports voip.py
```

Recomendado:
- `--lto=yes`
- `--clang`

---

## ▶️ Cómo Probar

1. PC A escucha:
```
python voip.py --listen --port 5000
```

2. PC B llama:
```
python voip.py --call <IP_DEL_PC_A> --port 5000
```

Si se escuchan ⇒ funciona el audio P2P.

---

## 🗣 Qué comentarios busco

Lo útil para mí es:

✔ pruebas en red real  
✔ logs de errores  
✔ NAT issues  
✔ delay / jitter  
✔ uso CPU / RAM  
✔ ideas sobre audio / codecs  
✔ PRs de mejora  

No busco “bonito proyecto”, busco **críticas técnicas**.

---

## 🤝 Contribuir

Pull Requests = **Bienvenidos**  
Issues = **Aún mejor**

Setup rápido:

```bash
git clone <repo>
pip install -r requirements.txt
python voip.py --help
```

---

## 📬 Feedback

Abre un **Issue** o PR en el repo y comenta tu experiencia.  
> No me interesan estrellas, me interesa tu **feedback técnico** sobre VoIP y P2P.
