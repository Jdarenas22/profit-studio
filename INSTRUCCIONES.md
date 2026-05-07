# Cómo usar este kit

## Requisitos

1. **Visual Studio Code** instalado (descarga en https://code.visualstudio.com)
2. **Extensión de Claude Code** instalada desde el marketplace de VS Code (busca "Claude Code" en extensiones)

## Paso 1 — Abrir este proyecto

1. Abre VS Code
2. Archivo → Abrir carpeta → selecciona esta carpeta (`kit-web-scrolling`)
3. Abre Claude Code desde el panel lateral de VS Code

## Paso 2 — Pedir tu web

Escribe algo como:

> Hazme una web para mi negocio

Claude te irá preguntando los datos. Ten a mano:
- Nombre de tu negocio
- Dirección y ciudad
- Lista de servicios con precios
- Teléfono y email
- Horarios
- Reseñas de clientes (si tienes)
- Instagram u otras redes (si tienes)

## Paso 3 — Ver el resultado

Claude genera un archivo HTML y lo abre en tu navegador automáticamente. Si quieres cambiar algo, díselo:

> "Cambia el color principal a azul"
> "Añade una sección de precios"
> "El horario está mal, los sábados cerramos a las 15:00"

## Paso 4 — Publicar tu web (opcional)

El archivo HTML generado lo puedes subir a cualquier hosting:
- Arrastra el archivo a Netlify (netlify.com) — gratis
- Súbelo a tu hosting actual

## Estructura

```
kit-web-scrolling/
├── CLAUDE.md                        <- Claude lo lee automáticamente
├── INSTRUCCIONES.md                 <- Este archivo
├── .claude/
│   └── skills/
│       └── 01-web-scrolling.md      <- La skill
└── assets/                          <- Pon aquí tus fotos y vídeos (opcional)
```
