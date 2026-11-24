# ProFit Studio

Plataforma web de gestión para un estudio de entrenamiento personal en Colombia. Reúne en una sola
aplicación a la entrenadora, su equipo y sus clientas: membresías, pagos, valoraciones físicas, rutinas,
seguimiento de medidas y **planes de ejercicio y alimentación con apoyo de inteligencia artificial**,
siempre revisados por un entrenador antes de que la clienta los vea.

[![CI](https://github.com/Jdarenas22/profit-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/Jdarenas22/profit-studio/actions/workflows/ci.yml)

- **Producción:** <https://profit-studio-production.up.railway.app>
- **Stack:** Python 3.11 · Django 5.2 LTS · PostgreSQL · Tailwind CSS · HTMX · Alpine.js · Railway
- **Mercado:** Colombia (COP, zona horaria `America/Bogota`, Ley 1581 de 2012 y Ley 2300 de 2023)

---

## Contenido

- [Funcionalidades](#funcionalidades)
- [Inteligencia artificial](#inteligencia-artificial)
- [Tecnologías](#tecnologías)
- [Arquitectura](#arquitectura)
- [Integraciones externas](#integraciones-externas)
- [Puesta en marcha local](#puesta-en-marcha-local)
- [Pruebas y calidad](#pruebas-y-calidad)
- [Ejecución única y gratuita](#ejecución-única-y-gratuita)
- [Despliegue](#despliegue)
- [Seguridad y privacidad](#seguridad-y-privacidad)
- [Estado y limitaciones](#estado-y-limitaciones)
- [Documentación](#documentación)

---

## Funcionalidades

**Para la clienta**
- Registro, inicio de sesión y recuperación de contraseña.
- Panel personal con su rutina, membresía, medidas y progreso.
- Rutinas multi-día con imágenes y videos de YouTube; marca los días completados.
- Registro de medidas corporales (peso, estatura, IMC) con historial.
- Ficha de salud y alimentación con consentimiento versionado; puede exportar o borrar sus datos.
- **Camino guiado** hasta su plan con IA: ve en qué paso va y qué le falta.
- Plan de alimentación con 3 opciones por comida, intercambios, lista de compras y «Cumplí esta comida».
- Pagos en línea con Wompi y avisos opcionales por WhatsApp.
- Aplicación instalable en el teléfono (PWA).

**Para el equipo de entrenadores**
- Gestión de clientas (cada entrenador ve solo las asignadas; la dueña ve todas y las asigna).
- Valoración inicial (IMC) y test de Ruffier / Ruffier-Dickson mediante una API interna.
- Constructor interactivo de rutinas y banco de ejercicios.
- Bandeja «Planes por revisar»: revisa, edita, aprueba o regenera los borradores de la IA.
- Pagos manuales (efectivo o transferencia) con comprobantes en almacenamiento privado.
- Desactivar, reactivar y eliminar cuentas con auditoría.

---

## Inteligencia artificial

La IA (Google Gemini) cumple un rol **acotado y supervisado**: propone borradores; no decide ni publica.

```
Ficha + medidas + consentimientos
        │
        ▼
Motor de reglas en Python ──► ¿se puede generar? (consentimientos, banderas de salud, cupos)
        │                     calcula energía y macros (Mifflin-St Jeor)
        │                     filtra ejercicios / alimentos / recetas seguras
        ▼
Gemini (REST, JSON con esquema) ──► elige ejercicios o recetas de una lista cerrada
        │
        ▼
Validación en Python ──► descarta lo que no cumpla; un reintento con los códigos de error
        │
        ▼
Borrador ──► el entrenador revisa y aprueba ──► la clienta lo ve
```

- **Qué hace la IA:** arma la estructura de la rutina (días, ejercicios, series, descansos) o elige, por
  cada comida, 3 recetas de un recetario de 117 preparaciones.
- **Qué hace Python:** decide si se puede generar, calcula calorías y macronutrientes, filtra lo permitido
  y valida todo lo que devuelve la IA.
- **Privacidad:** a la IA no viajan nombre, correo, teléfono, identificadores, fecha de nacimiento,
  diagnósticos ni texto libre de la ficha. No se registran prompts ni respuestas.
- **Modos** (`AI_PLANS_MODE`): `off` (por defecto en producción), `simulated` (desarrollo, sin red),
  `live` (Gemini real con nivel Free o pago explícitamente confirmado). No hay una aplicación demo.
- **No hay chatbot ni modelos de intención:** la interacción es por formularios y acciones explícitas.

Detalle técnico: [`docs/contracts/plan-ia.md`](docs/contracts/plan-ia.md) y
[`docs/MENUS_BIENESTAR_GEMINI.md`](docs/MENUS_BIENESTAR_GEMINI.md).

---

## Tecnologías

| Capa | Tecnología |
| --- | --- |
| Lenguaje y framework | Python 3.11, Django 5.2 LTS (monolito modular con plantillas del servidor) |
| Base de datos | PostgreSQL en producción (14 o superior), SQLite en desarrollo |
| Servidor | Gunicorn + WhiteNoise (estáticos con huella y caché de un año) |
| Frontend | Plantillas Django, Tailwind CSS 3 (compilado en local), HTMX, Alpine.js, Font Awesome |
| Archivos | Cloudflare R2 (S3) con un bucket privado para comprobantes |
| Seguridad | django-axes, CSRF, HSTS, Permissions-Policy, SRI en CDN |
| Calidad | Pruebas de Django (más de 1.700), GitHub Actions, pip-audit |
| Despliegue | Railway (Nixpacks) |

Las integraciones HTTP (Gemini, WhatsApp, USDA) usan la biblioteca estándar `urllib`: sin SDK de terceros,
con la clave en cabecera, sin seguir redirecciones y con lectura limitada.

---

## Arquitectura

```
profit-studio/
├── apps/
│   ├── accounts/      Usuarios, roles, paneles, permisos y ciclo de vida de cuentas
│   ├── memberships/   Planes y membresías
│   ├── payments/      Wompi y pagos manuales
│   ├── exercises/     Banco de ejercicios
│   ├── routines/      Rutinas multi-día
│   ├── assessments/   Valoraciones, medidas y API Ruffier-Dickson
│   ├── health/        Consentimientos, ficha de salud y registro de accesos
│   ├── plans/         Planes con IA: reglas, cliente Gemini, cola de trabajos, nutrición
│   ├── whatsapp/      Avisos por WhatsApp Cloud API
│   └── public/        Sitio público, PWA y diagnóstico de infraestructura
├── config/            Settings (base, development, production), URLs y middleware
├── templates/         Plantillas HTML
├── static/            CSS compilado, JS, imágenes, service worker
├── frontend/          Fuente de Tailwind
├── docs/              Contratos técnicos, despliegue, auditorías
└── scripts/           Compilación de CSS y arranque único de la aplicación
```

**Roles:** `trainer` y `member`. La entrenadora principal (`is_superuser`) ve todas las clientas, pagos y
entrenadores; los demás entrenadores solo ven a sus clientas asignadas. Toda vista que recibe el id de una
clienta pasa por `apps/accounts/permissions.py` y responde 404 si no es accesible.

**Tareas en segundo plano:** cola en la base de datos con hilos y recuperación de trabajos huérfanos, sin
Celery. Los avisos de WhatsApp tienen además un cron de Railway como respaldo.

---

## Integraciones externas

| Servicio | Uso | Cómo se consume |
| --- | --- | --- |
| Google Gemini | Borradores de rutinas y menús | `POST generateContent` con JSON y esquema; clave en `x-goog-api-key` |
| Wompi | Pagos en línea | Firma de integridad SHA-256 y webhook con firma verificada |
| WhatsApp Business Cloud API | Avisos de cuenta (sin datos de salud) | Graph API con token *Bearer*; webhook con `X-Hub-Signature-256` |
| USDA FoodData Central | Datos nutricionales | Búsqueda e importación revisada por comandos de gestión |
| Cloudflare R2 | Fotos, ejercicios y comprobantes | boto3 / django-storages; URLs firmadas para comprobantes |
| SMTP (Gmail) | Correos de la plataforma | TLS por el puerto 587 |

Todas están **apagadas o vacías por defecto** y se activan con variables de entorno.

---

## Puesta en marcha local

Requisitos: Python 3.11 y Git. Node solo si vas a cambiar clases de Tailwind.

```powershell
git clone https://github.com/Jdarenas22/profit-studio.git
cd profit-studio
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements/development.txt
copy .env.example .env      # valores de ejemplo; nunca subas tu .env
python manage.py migrate --settings=config.settings.development
python manage.py load_initial_data --settings=config.settings.development
python manage.py load_foods --settings=config.settings.development
python manage.py runserver --settings=config.settings.development
```

Abre <http://127.0.0.1:8000>. En desarrollo la IA y WhatsApp funcionan en modo simulado (sin red ni
costo) y los correos se imprimen en la consola.

Para crear la cuenta de la entrenadora principal (la contraseña se pide por consola o se lee de
`TRAINER_INITIAL_PASSWORD`; nunca va en el comando):

```powershell
python manage.py create_trainer --username entrenadora --first-name Nombre --last-name Apellido --email correo@ejemplo.com --superuser --settings=config.settings.development
```

**CSS:** después de cambiar clases de Tailwind, recompila y sube `static/css/app.css`:

```powershell
.\scripts\build-css.ps1
```

**Comandos útiles**

| Comando | Para qué |
| --- | --- |
| `check_infra` | Revisa la configuración (base, R2, Wompi, correo, IA) sin mostrar secretos |
| `check_gemini` | Prueba la conexión con Gemini usando datos ficticios |
| `process_plan_jobs` | Recupera trabajos de IA huérfanos |
| `list_untagged_exercises` | Ejercicios sin etiquetas para los planes con IA |
| `fdc_search` / `fdc_import` | Busca e importa alimentos de USDA (vista previa por defecto) |
| `check_whatsapp` / `process_whatsapp_queue` | Diagnóstico y envío de avisos por WhatsApp |
| `purge_orphan_files` | Cuenta o borra archivos que ninguna fila usa |

---

## Pruebas y calidad

```powershell
python manage.py check --settings=config.settings.development
python manage.py makemigrations --check --dry-run --settings=config.settings.development
python -W error::DeprecationWarning manage.py test --settings=config.settings.development
```

- Más de 1.700 pruebas automatizadas; **ninguna usa proveedores reales** (clientes simulados de IA,
  WhatsApp y pagos).
- GitHub Actions ([`ci.yml`](.github/workflows/ci.yml)) corre en cada *push* y *pull request*: `check`,
  migraciones pendientes, la suite completa y `pip-audit`.

---

## Ejecución única y gratuita

La aplicación normal utiliza una sola base `db.sqlite3`, sus cuentas existentes y el puerto 8000.
Gemini Free selecciona recetas de catálogo; Django conserva la ficha, calcula porciones y revisa el día completo.

```powershell
.\scripts\start.ps1             # Aplicación real en http://127.0.0.1:8000/
.\scripts\start.ps1 -Public     # La misma aplicación también por HTTPS en Internet
```

Requiere `requirements/local.txt`, la clave privada del backend y nivel Free confirmado en `.env`.
La publicación usa Cloudflare Quick Tunnel: no requiere comprar dominio ni hosting, pero el PC
debe permanecer encendido y la dirección cambia al reiniciar. No se inicia otra web o base.
Guía: [ejecución integrada](docs/EJECUCION-INTEGRADA.md).

---

## Despliegue

Railway despliega automáticamente cada cambio en `main`. La configuración vive en el panel de Railway
(no hay `railway.toml`): build con `collectstatic`, arranque con `bash start.sh` y healthcheck en
`/health/`. `start.sh` espera a la base, migra, prepara los estáticos, crea la cuenta inicial y arranca
Gunicorn.

Variables obligatorias en producción: `SECRET_KEY`, `DATABASE_URL` y `ALLOWED_HOSTS`. Las demás (R2,
Wompi, correo, Gemini, WhatsApp, Redis) son opcionales y están documentadas en
[`.env.example`](.env.example) y [`docs/DEPLOY.md`](docs/DEPLOY.md).

Flujo de trabajo: cada cambio entra por *pull request* con el CI en verde.

---

## Seguridad y privacidad

- Autorización por clienta en todas las vistas; lo no accesible responde 404.
- Bloqueo tras intentos fallidos de inicio de sesión y límite en la recuperación de contraseña.
- Webhooks (Wompi, WhatsApp) con firma verificada e idempotencia.
- Comprobantes de pago en un bucket privado con URLs firmadas de 5 minutos.
- Datos de salud solo con consentimiento vigente y versionado; registro de accesos a la ficha;
  exportación y borrado a pedido de la clienta.
- El service worker solo guarda estáticos: nunca páginas privadas.
- Los secretos viven en variables de entorno; `production.py` no arranca con valores de ejemplo.

---

## Estado y limitaciones

- En producción, las funciones de salud, IA y WhatsApp están **apagadas por defecto** hasta completar
  la revisión legal de los consentimientos y la validación profesional.
- El catálogo de alimentos usa **cifras aproximadas sin validar** por un profesional; los umbrales de
  las banderas de salud están marcados *[A VALIDAR]*.
- La alimentación clínica (enfermedades, medicamentos, embarazo) está pausada: el alcance actual es
  bienestar para adultos.
- La IA propone y el entrenador aprueba: ningún plan llega a la clienta sin revisión humana.

---

## Documentación

| Documento | Contenido |
| --- | --- |
| [`DOCUMENTACION.md`](DOCUMENTACION.md) | Guía técnica detallada de cada app |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | Despliegue en Railway, variables y diagnóstico |
| [`docs/contracts/`](docs/contracts) | Contratos técnicos: planes con IA, WhatsApp, cuentas, fase D |
| [`docs/MENUS_BIENESTAR_GEMINI.md`](docs/MENUS_BIENESTAR_GEMINI.md) | Menús de bienestar, recetario y variedad |
| [`docs/API_RUFFIER_DICKSON.md`](docs/API_RUFFIER_DICKSON.md) | API interna de evaluación cardiovascular |
| [`docs/INTEGRACION_DATOS_NUTRICIONALES.md`](docs/INTEGRACION_DATOS_NUTRICIONALES.md) | Integración con USDA FoodData Central |
| [`docs/auditorias/`](docs/auditorias) | Auditoría de seguridad y capacidad, y sus correcciones |

---

Proyecto académico de prácticas desarrollado con apoyo de asistentes de IA (Claude Code y ChatGPT),
con revisión humana de cada cambio.
