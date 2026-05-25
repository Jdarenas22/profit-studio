# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Proyecto

**ProFit Studio** — Plataforma web de gestión para gimnasio/entrenamiento personal. Django 5.0.6, desplegado en Railway con PostgreSQL. El negocio opera en Colombia (moneda COP, zona horaria `America/Bogota`).

## Comandos esenciales

```bash
# Desarrollo local
python manage.py runserver                          # Settings: base.py (DEBUG desde .env)
DJANGO_SETTINGS_MODULE=config.settings.production python manage.py runserver  # Simular producción

# Base de datos
python manage.py migrate
python manage.py load_initial_data   # Crea categorías de ejercicios y planes de membresía
python manage.py create_trainer --username yiseth --first-name Yiseth --last-name "Misas García" \
    --email profitstudio075@gmail.com --password "xxx" --superuser

# Producción (Railway ejecuta start.sh automáticamente)
# El flujo es: migrate → collectstatic → create_trainer → load_initial_data → gunicorn
```

## Arquitectura

### Estructura de apps (`/apps/`)

| App | Responsabilidad |
| --- | --- |
| `accounts` | Usuario custom, auth, dashboard, panel de entrenadores |
| `memberships` | Planes y membresías (activar, renovar, vencer) |
| `exercises` | Banco de ejercicios con imagen/video YouTube |
| `routines` | Rutinas multi-día asignadas a clientes |
| `assessments` | Valoración inicial (IMC + Test Ruffier-Dickson) y mediciones corporales |
| `payments` | Pagos online (Wompi) y manuales (efectivo/transferencia) |
| `public` | Páginas públicas (home, nosotros, contacto, testimonios) |

### Modelo de roles

El modelo `User` extiende `AbstractUser` con campo `role` ('trainer' / 'member'). Dentro de los trainers, `is_superuser=True` identifica a Yiseth (dueña). Esta distinción determina:

- Solo `is_superuser` ve Pagos y Entrenadores en el nav del panel trainer
- Solo `is_superuser` puede crear/editar otros trainers y asignar clientes
- `is_superuser` ve todos los clientes; otros trainers solo ven los asignados (`assigned_trainer=request.user`)

```python
# Propiedades clave del User
user.is_trainer   # True si role == 'trainer'
user.has_active_membership  # True si membership.is_valid
user.assigned_trainer  # FK a otro User (trainer)
user.assigned_clients  # reverse relation (clientes asignados a este trainer)
```

### Decoradores de acceso

```python
@trainer_required        # is_authenticated + is_trainer; 403 si no
@membership_required     # trainers pasan siempre; members necesitan membresía válida
@login_required          # estándar Django
```

Para vistas solo de superusuario no hay decorador dedicado — se hace con `if not request.user.is_superuser: redirect('trainer_dashboard')` al inicio de la vista.

### Settings

```text
config/settings/
├── base.py         # Base común (no define DEBUG, ALLOWED_HOSTS — vienen de .env)
└── production.py   # Extiende base; fuerza DEBUG=False, configura R2, PostgreSQL, email
```

No existe `local.py`. La selección del settings file es via `DJANGO_SETTINGS_MODULE` (default en `start.sh`: `config.settings.production`).

Variables de entorno requeridas en Railway:

- `SECRET_KEY`, `DATABASE_URL`, `ALLOWED_HOSTS`
- `TRAINER_PASSWORD` (contraseña inicial de Yiseth)
- R2: `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_ENDPOINT_URL`, `R2_PUBLIC_URL`
- Wompi: `WOMPI_PUBLIC_KEY`, `WOMPI_PRIVATE_KEY`, `WOMPI_INTEGRITY_SECRET`, `WOMPI_EVENTS_SECRET`
- Email: `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`
- `WHATSAPP_NUMBER`, `WHATSAPP_LINK`, `INSTAGRAM_URL`

### Almacenamiento de archivos media

- **Sin R2 configurado**: guarda en `BASE_DIR/media/` (se pierde con cada redeploy en Railway)
- **Con R2**: usa `django-storages[s3]` + boto3 con `AWS_QUERYSTRING_AUTH=False` (URLs permanentes). `R2_ENDPOINT_URL` es el endpoint S3 API de boto3; `R2_PUBLIC_URL` es la URL pública del bucket (e.g., `https://pub-xxx.r2.dev`)

### Frontend

- **CSS**: Tailwind CDN (sin build step)
- **JS interactivo**: Alpine.js (directivas `x-data`, `x-model`, `x-show`)
- **Tema**: dark (`bg-zinc-900`/`bg-black`), color acento `#FF6B00` (`text-primary`, `bg-primary`)
- **Templates**: dos base templates — `base.html` (público y miembros) y `trainer/base.html` (panel admin)
- **Parciales reutilizables**: `templates/partials/` (navbar, footer, whatsapp_btn)
- **Context processor global**: `apps/public/context_processors.site_settings` inyecta `WHATSAPP_LINK`, `WHATSAPP_NUMBER`, `INSTAGRAM_URL` en todos los templates

### Pagos (dos sistemas independientes)

1. **Wompi** (`apps/payments`): pagos online con webhook. La vista `wompi_webhook` es `@csrf_exempt`. El `Payment` se crea al iniciar checkout; el webhook actualiza el status y activa la membresía.
2. **ManualPayment** (`apps/payments`): registrado por trainers para pagos en efectivo/transferencia. Solo visible a `is_superuser` en el nav.

### Cálculos automáticos en save()

- `InitialAssessment.save()` → calcula y guarda `imc` e `imc_classification`
- `DixonTest.save()` → calcula `index_value` y `classification` (IRD = (P0+P1+P2-200)/10)
- `BodyMeasurement.save()` → calcula `imc` e `imc_classification`

### Ejercicios: prioridad de media

El template `exercises/detail.html` sigue este orden:

1. `image` + `video_url` → imagen clickeable con overlay play que abre YouTube (tab nueva)
2. Solo `video_url` → iframe embed
3. Solo `video_file` → player HTML5
4. Solo `image` → imagen estática
5. Nada → placeholder

El modelo `Exercise` tiene `youtube_embed_url` property que convierte cualquier URL de YouTube al formato embed.

### Rutinas

Estructura jerárquica: `Routine` → `RoutineDay` (días) → `RoutineExercise` (ejercicios con sets/reps/descanso). El builder del trainer es interactivo (Alpine.js). Los clientes ven sus rutinas en `member_routine.html` con thumbnails de imagen y botón de video YouTube directo.
