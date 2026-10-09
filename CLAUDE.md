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
    --email profitstudio075@gmail.com --superuser
# La contraseña se lee de TRAINER_INITIAL_PASSWORD (o se pide por consola); nunca va en el comando

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
| `assessments` | Valoración inicial (IMC + Test Ruffier-Dickson) y mediciones corporales (las registran el entrenador o la propia clienta; `services.py` tiene los casos de uso) |
| `health` | Datos de salud de la clienta: textos de consentimiento versionados (`ConsentTextVersion`, solo superusuaria en el admin) y registro de autorizaciones (`ConsentRecord`). La ficha de salud llega en la fase A2 (ver `docs/contracts/plan-ia.md`) |
| `payments` | Pagos online (Wompi) y manuales (efectivo/transferencia) |
| `public` | Páginas públicas (home, nosotros, contacto, testimonios) |

### Modelo de roles

El modelo `User` extiende `AbstractUser` con campo `role` ('trainer' / 'member'). Dentro de los trainers, `is_superuser=True` identifica a Yiseth (dueña). Esta distinción determina:

- Solo `is_superuser` ve Pagos y Entrenadores en el nav del panel trainer
- Solo `is_superuser` puede crear/editar otros trainers y asignar clientes
- `is_superuser` ve todos los clientes y es la única que los asigna; otros trainers ven SOLO los asignados (`assigned_trainer=request.user`). Los clientes auto-registrados quedan sin asignar (solo los ve la superusuaria); los que crea un trainer con "Agregar cliente" quedan asignados a ese trainer
- Toda vista que reciba el pk de un cliente (o de una valoración, rutina, membresía, medición o pago manual) debe usar `apps/accounts/permissions.py` (`get_client_for_trainer`, `clients_for_trainer`, `ensure_client_access`); lo no accesible responde 404

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
@superuser_required      # solo la entrenadora principal (trainer + is_superuser)
@member_required         # solo la clienta con membresía vigente (datos de salud); el entrenador recibe 403
```

El cierre de sesión (`logout`) solo acepta POST: se hace con un formulario con `{% csrf_token %}`.

### Settings

```text
config/settings/
├── base.py         # Base común (no define DEBUG, ALLOWED_HOSTS — vienen de .env)
└── production.py   # Extiende base; fuerza DEBUG=False, configura R2, PostgreSQL, email
```

No existe `local.py`. La selección del settings file es via `DJANGO_SETTINGS_MODULE` (default en `start.sh`: `config.settings.production`).

Variables de entorno requeridas en Railway:

- `SECRET_KEY`, `DATABASE_URL`, `ALLOWED_HOSTS`
- `SECRET_KEY` y `ALLOWED_HOSTS` son OBLIGATORIAS: `production.py` no arranca si faltan, si ALLOWED_HOSTS tiene `*` o si SECRET_KEY es un valor de ejemplo
- `TRAINER_INITIAL_PASSWORD` (opcional; también se acepta el nombre antiguo `TRAINER_PASSWORD`): si existe, `start.sh` crea la superusuaria `yiseth` con esa contraseña. Sin ella no se crea la cuenta. Nunca hay contraseña por defecto en el código
- R2: `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_ENDPOINT_URL`, `R2_PUBLIC_URL`, `R2_RECEIPTS_BUCKET_NAME` (bucket PRIVADO para comprobantes)
- Wompi: `WOMPI_PUBLIC_KEY`, `WOMPI_PRIVATE_KEY`, `WOMPI_INTEGRITY_SECRET`, `WOMPI_EVENTS_SECRET`
- Email: `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`
- `WHATSAPP_NUMBER`, `WHATSAPP_LINK`, `INSTAGRAM_URL`
- `HEALTH_FEATURES_ENABLED` (opcional): enciende las pantallas de salud de la clienta ("Mis medidas", consentimiento). **Apagada por defecto en producción** (las rutas responden 404) hasta que un abogado apruebe los textos de consentimiento y la superusuaria publique la versión definitiva (Admin > Textos de consentimiento); `development.py` la enciende. `PRIVACY_POLICY_URL` (opcional): enlace público a la política de tratamiento de datos que se muestra junto al consentimiento

### Almacenamiento de archivos media

- **Sin R2 configurado**: guarda en `BASE_DIR/media/` (se pierde con cada redeploy en Railway)
- **Con R2**: usa `django-storages[s3]` + boto3 con `AWS_QUERYSTRING_AUTH=False` (URLs permanentes). `R2_ENDPOINT_URL` es el endpoint S3 API de boto3; `R2_PUBLIC_URL` es la URL pública del bucket (e.g., `https://pub-xxx.r2.dev`, https y sin ruta). django-storages NO usa `MEDIA_URL`: `production.py` toma el host de `R2_PUBLIC_URL` como `AWS_S3_CUSTOM_DOMAIN` para que `FieldFile.url` salga como `https://pub-xxx.r2.dev/<archivo>` (sin el bucket en la ruta). Sin `R2_PUBLIC_URL` las URLs apuntan al endpoint de la API y no se ven en el navegador. Una `R2_PUBLIC_URL` mal formada (sin https, con ruta) detiene el arranque.
- **Comprobantes de pago (`receipts/`) nunca son públicos**: `ManualPayment.receipt` usa `apps/payments/storage.py` (con R2: bucket aparte `R2_RECEIPTS_BUCKET_NAME`, sin dominio público: `custom_domain=None` y `querystring_auth=True` explícitos, URLs firmadas de 5 min contra el endpoint de la API) y solo se entregan por la vista `payment_receipt`. `/media/receipts/...` responde 404 (`config/media.py`).

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

### Salud y consentimiento de la clienta (fase A1 de `docs/contracts/plan-ia.md`)

- La clienta registra sus propias medidas (`/assessments/me/measurements/`, rutas `member_measurement_*`) solo con membresía vigente, función encendida (`HEALTH_FEATURES_ENABLED`) y consentimiento `health_data` vigente. Siempre trabaja sobre `request.user`; ninguna ruta de clienta recibe el id de otra persona. Puede borrar solo sus mediciones del mismo día (`source='member'`); el entrenador edita/borra las de sus clientes (`trainer_measurement_edit/delete`, con `ensure_client_access`).
- Un consentimiento solo vale si no está retirado y su texto sigue siendo el vigente; al publicar una versión nueva hay que aceptarla de nuevo. Los textos iniciales (v1) son un BORRADOR con marcadores `[RAZÓN SOCIAL]`, `[NIT]`, `[CORREO DE DERECHOS]` (migración `health` 0002). La fase B debe llamar `apps.health.services.ai_texts_ready()` antes de enviar nada a la IA: en producción devuelve falso mientras queden marcadores `[...]`.
- Menores de 18 (por la edad de la última valoración inicial; la fecha de nacimiento llega con la ficha en A2): no registran medidas ni dan consentimiento propio.
- Las vistas de salud de la clienta responden `Cache-Control: no-store`, sin datos de salud en la URL ni en los logs.

