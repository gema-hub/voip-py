# 📞 voip-py — Llamadas VoIP desde PC

> Aplicación **VoIP en Python** para realizar **llamadas normales desde PC**.  
> Compilada con **Nuitka** para binarios nativos.  
> **No busco estrellas** → busco **comentarios, issues y PRs técnicos**.

---

## 🚀 ¿Qué es esto?

`voip-py` permite realizar llamadas VoIP usando protocolos estándar (ej: SIP/RTP) directamente desde PC.  
La idea del proyecto es experimentar con **telefonía IP**, **audio en tiempo real** y **empaquetado con Nuitka**.

Este repo es ideal si te interesa:
- VoIP / SIP / RTP
- Audio en tiempo real
- Python + multimedia
- Telefonía IP desde desktop
- Compilación con Nuitka

---

## 🛠 Tecnologías

- Python ≥ 3.10
- Nuitka (compilado)
- (Opcional) `pjsua`, `pjsip`, `aiortc`, `sounddevice`, etc.
- Audio: `pyaudio` / `sounddevice`
- Codec: depende del stack de audio que uses (G.711 / Opus / etc)

---

## 📦 Compilación con Nuitka

Ejemplo:

```bash
python -m nuitka --standalone --onefile --remove-output \
    --follow-imports \
    voip.py
```

Opcionales útiles para performance:

- `--lto=yes` → optimización de enlace
- `--clang` → usa clang si está disponible
- `--enable-console` → modo debug
- `--disable-console` → modo producción GUI

---

## ▶️ Ejecución

Windows:

```
./build/voip.exe
```

Linux:

```
chmod +x voip && ./voip
```

---

## 🗣 Feedback que busco

Busco **problemas reales y comentarios técnicos**, como:

✔ Latencia de audio  
✔ Compatibilidad con dispositivos (micrófonos/headsets)  
✔ Problemas con SIP o RTP  
✔ Rendimiento tras compilar con Nuitka  
✔ Tamaño del binario  
✔ Issues de paquetes o dependencias  

Si probaste y algo falló → **abre un Issue**, ese es el objetivo del repo.

---

## 📝 Roadmap / TODO

- [ ] Mejorar manejo de audio (buffering / jitter)
- [ ] Soporte para más códecs
- [ ] Marcador (dialpad) GUI con teclado
- [ ] Identificador de llamadas
- [ ] Contactos
- [ ] Mejorar empaquetado (NSIS/DEB/AppImage)
- [ ] CI/CD con Nuitka
- [ ] WebRTC (posible) vía `aiortc`

Si quieres ayudar → haz PR o Issue.

---

## 🤝 Contribuciones

**PRs** y **Issues** están abiertos.  
Setup rápido:

```bash
git clone <repo>
pip install -r requirements.txt
python voip.py   # test antes de compilar
```

---

## 🙌 Estado del proyecto

📌 **En desarrollo**  
🔍 **Buscando testers técnicos**  
📨 **Aportaciones abiertas**

---

## 📬 Contacto / Feedback

Abre un **Issue**, PR o comenta en el repo.

> No busco estrellas — busco **comentarios técnicos** que me hagan mejorar el proyecto.
