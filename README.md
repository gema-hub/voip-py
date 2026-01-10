# 🧩 voip-py

> Ejecutable compilado con **Nuitka**. No busco estrellas, busco **comentarios, issues y PRs** con feedback técnico.

---

## 🚀 Motivo del Proyecto

- Python es rápido para desarrollar pero difícil de distribuir sin entorno.
- Nuitka permite compilar a **binarios nativos** con buena compatibilidad.
- Quiero mejorar packaging, distribución y rendimiento.

Si tienes experiencia en:
- Optimización
- Distribución binaria
- Seguridad / ofuscación
- Python Packaging

👉 Me interesa tu feedback.

---

## 🛠 Tecnologías

- Python ≥ 3.12
- Nuitka (compilador Python → C)
- (Dependencias opcionales: …)

---

## 📦 Compilación con Nuitka

Ejemplo real:

```bash
python -m nuitka --standalone --onefile --remove-output \
    --enable-plugin=tk-inter \
    --follow-imports win_client.py
```

Opciones opcionales útiles:
- `--lto=yes` → Link-Time Optimization
- `--clang` → usar Clang si está disponible
- `--enable-console` / `--disable-console`

---

## ▶️ Ejecución

Windows:

```
./build/main.exe
```

Linux:

```
chmod +x main && ./main
```

---

## 📝 Roadmap / TODO

- [ ] Reducir tamaño del ejecutable
- [ ] Benchmark rendimiento
- [ ] Empaquetado (NSIS / Deb / AppImage)
- [ ] CI/CD con GitHub Actions + Nuitka
- [ ] Tests unitarios
- [ ] Documentar plugins Nuitka
