# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Proyecto

**ProFit Studio** — Plataforma web de gestión para gimnasio/entrenamiento personal. Django 5.2 LTS (5.2.19), desplegado en Railway con PostgreSQL (**Django 5.2 exige PostgreSQL 14 o superior**: con una versión anterior el servidor no arranca). El negocio opera en Colombia (moneda COP, zona horaria `America/Bogota`).

## Ubicación principal de trabajo

La copia activa del proyecto está en `C:\Proyectos\profit-studio`. Ejecutar desde esa carpeta los comandos, pruebas y migraciones. La carpeta anterior de OneDrive es una copia de respaldo; no usarla como destino de nuevos cambios.

## Comandos esenciales

```bash
# Desarrollo local
python manage.py runserver --settings=config.settings.development
python manage.py check --settings=config.settings.development
python manage.py test --settings=config.settings.development

# Base de datos
python manage.py migrate
python manage.py load_initial_data   # Crea categorías de ejercicios y planes de membresía
python manage.py create_trainer --username yiseth --first-name Yiseth --last-name "Misas García" \
    --email profitstudio075@gmail.com --superuser
# La contraseña se lee de TRAINER_INITIAL_PASSWORD (o se pide por consola); nunca va en el comando

# Producción (Railway ejecuta start.sh automáticamente)
# El flujo es: migrate → collectstatic → create_trainer → load_initial_data → gunicorn
```

Abre en VS Code la carpeta que contiene este archivo (`manage.py` y `.git`), no la carpeta de respaldo exterior. El entorno virtual local vive en `.venv/`; los comandos de VS Code usan el intérprete de ese entorno. `.claude/settings.json` impide a Claude leer el `.env`, la base SQLite local, el archivo local de usuarios de prueba y los medios.

## Arquitectura

### Estructura de apps (`/apps/`)

| App | Responsabilidad |
| --- | --- |
| `accounts` | Usuario custom, auth, dashboard, panel de entrenadores |
| `memberships` | Planes y membresías (activar, renovar, vencer) |
| `exercises` | Banco de ejercicios con imagen/video YouTube |
| `routines` | Rutinas multi-día asignadas a clientes |
| `assessments` | Valoración inicial (IMC + Test Ruffier-Dickson) y mediciones corporales (las registran el entrenador o la propia clienta; `services.py` tiene los casos de uso) |
| `health` | Datos de salud de la clienta: textos de consentimiento versionados (`ConsentTextVersion`, solo superusuaria en el admin), registro de autorizaciones (`ConsentRecord`), ficha de salud y alimentación versionada (`HealthProfile`, fase A2) y registro de accesos a la ficha (`HealthAccessLog`). Ver `docs/contracts/plan-ia.md` |
| `plans` | Planes con IA (fase B: borrador de rutina; fase C: plan de alimentación, ambos con Google Gemini). Motor de reglas en Python puro (`rules/`: banderas, elegibilidad, filtro del catálogo, energía y macros, alimentos, validación de la salida), cliente de IA (`ai/`: simulado y Gemini), cola de trabajos (`jobs.py`), casos de uso (`services.py`, `nutrition.py`). Ver "Planes con IA" y "Plan de alimentación" abajo |
| `payments` | Pagos online (Wompi) y manuales (efectivo/transferencia) |
| `public` | Páginas públicas (home, nosotros, contacto, testimonios) |
| `whatsapp` | Avisos de la cuenta por WhatsApp (WhatsApp Business Cloud API de Meta): consentimiento `whatsapp_notices`, número confirmado, cola con reintentos, webhook firmado. Ver "Avisos por WhatsApp" abajo y `docs/contracts/whatsapp.md` |

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
- `SECURITY_STRICT_SECRETS` (opcional, `false` por defecto; un valor distinto de true/false detiene el arranque). Reglas en `config/security.py`. Por defecto solo AVISA cuando:
  - la `SECRET_KEY` no cumple `security.W009` (50+ caracteres, 5+ distintos, sin `django-insecure-`): chequeo `profit.W001`;
  - con R2, `R2_RECEIPTS_BUCKET_NAME` falta o es igual a `R2_BUCKET_NAME`: chequeo `profit.W002`, `RuntimeWarning` y `check_infra`.

  Los chequeos `profit.*` (`config/checks.py`) solo salen con `check --deploy`. Con `true`, `production.py` lanza `ImproperlyConfigured` y no arranca. Activarla en Railway solo después de ver `SECRET_KEY` y "Bucket de comprobantes" en OK en `check_infra`. Los mensajes nombran variables, nunca valores
- `REDIS_URL` (opcional): sin ella la caché es memoria por proceso de gunicorn. Solo el límite del registro de cuentas (`apps/accounts/ratelimit.py`) se cuenta por proceso; las sesiones y `django-axes` usan la base de datos. `check_infra` lo avisa en producción (área "Caché"). Con Redis, las sesiones viven allí: si Redis cae, nadie inicia sesión (`check_infra` hace un PING y da ERROR)
- `TRAINER_INITIAL_PASSWORD` (opcional; también se acepta el nombre antiguo `TRAINER_PASSWORD`): si existe, `start.sh` crea la superusuaria `yiseth` con esa contraseña. Sin ella no se crea la cuenta. Nunca hay contraseña por defecto en el código
- R2: `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_ENDPOINT_URL`, `R2_PUBLIC_URL`, `R2_RECEIPTS_BUCKET_NAME` (bucket PRIVADO para comprobantes)
- Wompi: `WOMPI_PUBLIC_KEY`, `WOMPI_PRIVATE_KEY`, `WOMPI_INTEGRITY_SECRET`, `WOMPI_EVENTS_SECRET`
- Email: `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`
- `WHATSAPP_NUMBER`, `WHATSAPP_LINK`, `INSTAGRAM_URL`
- Planes con IA (opcionales): `AI_PLANS_MODE` (`off` por defecto en producción, `simulated` en desarrollo, `live` = Gemini). En `live`, `production.py` NO arranca sin `GEMINI_API_KEY`, `GEMINI_MODEL` y `GEMINI_BILLING_CONFIRMED=true` (la clave debe ser de un proyecto con facturación: el nivel gratuito permite que Google use los datos). Otras: `GEMINI_TIMEOUT_SECONDS`, `GEMINI_SCHEMA_FIELD` (`responseJsonSchema` o `none`), `AI_PLANS_CLIENT_WEEKLY_LIMIT`, `AI_PLANS_TRAINER_DAILY_LIMIT`, `AI_PLANS_DAILY_JOB_LIMIT`, `AI_PLANS_ALT_SLOT_WEEKLY_LIMIT`, `AI_PLANS_ALT_DAILY_LIMIT` ("otra opción"), `AI_PLANS_RUN_INLINE` (pruebas)
- Avisos por WhatsApp (opcionales; NO confundir con `WHATSAPP_NUMBER`/`WHATSAPP_LINK`): `WHATSAPP_CLOUD_MODE` (`off` por defecto en producción, `simulated` en desarrollo, `live` = Meta). En `live`, `production.py` NO arranca sin `WHATSAPP_CLOUD_ACCESS_TOKEN`, `WHATSAPP_CLOUD_PHONE_NUMBER_ID`, `WHATSAPP_CLOUD_WABA_ID`, `WHATSAPP_CLOUD_APP_SECRET`, `WHATSAPP_CLOUD_VERIFY_TOKEN` (32+ caracteres) y `WHATSAPP_CLOUD_GRAPH_VERSION` (`vNN.N`). Pendientes legales (en `live` no se envía nada sin ellos): `WHATSAPP_CLOUD_SITE_URL` (https), `WHATSAPP_CLOUD_LEGAL_REVIEWED=true`, `PRIVACY_POLICY_URL` y texto `whatsapp_notices` sin marcadores. Otras: `WHATSAPP_CLOUD_TEMPLATE_LANG` (`es_CO`), `WHATSAPP_CLOUD_TIMEOUT_SECONDS`, `WHATSAPP_CLOUD_DAILY_LIMIT`, `WHATSAPP_CLOUD_EXPIRING_DAYS`, `WHATSAPP_CLOUD_PAYMENT_NOTICE`, `WHATSAPP_CLOUD_SEND_INLINE` (pruebas)
- `HEALTH_FEATURES_ENABLED` (opcional): enciende las pantallas de salud de la clienta ("Mis medidas", consentimiento). **Apagada por defecto en producción** (las rutas responden 404) hasta que un abogado apruebe los textos de consentimiento y la superusuaria publique la versión definitiva (Admin > Textos de consentimiento); `development.py` la enciende. `PRIVACY_POLICY_URL` (opcional): enlace público a la política de tratamiento de datos que se muestra junto al consentimiento

### Railway: configuración de despliegue

La configuración del servicio web vive en el **panel** de Railway (proyecto `alluring-dedication`, servicio `profit-studio`): builder Nixpacks, `collectstatic` en el build, arranque `bash start.sh`, healthcheck `/health/` (300 s) y reinicio «On Failure» (10). Los valores están en `docs/DEPLOY.md`, sección 9. `railway.toml` se retiró el 2026-11-17 (Railway deja de leerlo el 2026-12-01) y no se adoptó Infrastructure as Code (`.railway/railway.ts`: exige npm o un SDK en beta, y un `config apply` puede borrar recursos que falten en el archivo). No volver a crear `railway.toml` ni `railway.json` (una prueba lo impide). Red de seguridad: el `Procfile` es `web: bash start.sh`.

### Integración continua (`.github/workflows/ci.yml`)

En cada push y pull request, sin secretos ni despliegue (`permissions: contents: read`):

- Job "pruebas": Python 3.11 con caché de pip, `requirements/development.txt`, `check`, `makemigrations --check --dry-run` y `python -W error::DeprecationWarning manage.py test`. Node 24 con `actions/setup-node` para que la prueba de Tailwind compile con `npx` (sigue sin `package.json`).
- Job "dependencias": `pip-audit` sobre `requirements/base.txt`, `development.txt` y `local.txt`.

La suite no necesita variables de entorno: sin `.env`, development.py usa sus valores por defecto. Si una prueba nueva necesita una, se define en el workflow con un valor ficticio.

### Almacenamiento de archivos media

- **Sin R2 configurado**: guarda en `BASE_DIR/media/` (se pierde con cada redeploy en Railway)
- **Con R2**: usa `django-storages[s3]` + boto3 con `AWS_QUERYSTRING_AUTH=False` (URLs permanentes). `R2_ENDPOINT_URL` es el endpoint S3 API de boto3; `R2_PUBLIC_URL` es la URL pública del bucket (e.g., `https://pub-xxx.r2.dev`, https y sin ruta). django-storages NO usa `MEDIA_URL`: `production.py` toma el host de `R2_PUBLIC_URL` como `AWS_S3_CUSTOM_DOMAIN` para que `FieldFile.url` salga como `https://pub-xxx.r2.dev/<archivo>` (sin el bucket en la ruta). Sin `R2_PUBLIC_URL` las URLs apuntan al endpoint de la API y no se ven en el navegador. Una `R2_PUBLIC_URL` mal formada (sin https, con ruta) detiene el arranque.
- **Comprobantes de pago (`receipts/`) nunca son públicos**: `ManualPayment.receipt` usa `apps/payments/storage.py` (con R2: bucket aparte `R2_RECEIPTS_BUCKET_NAME`, sin dominio público: `custom_domain=None` y `querystring_auth=True` explícitos, URLs firmadas de 5 min contra el endpoint de la API) y solo se entregan por la vista `payment_receipt`. `/media/receipts/...` responde 404 (`config/media.py`).

### Frontend

- **CSS**: Tailwind CSS v3.4.19 compilado en local (Railway no compila). Fuente: `frontend/tailwind.css` + `tailwind.config.js` (tema, `content` y `safelist` para clases armadas por partes en las plantillas). Salida en git: `static/css/app.css` (minificado; en `base.html` va después de Font Awesome para que las utilidades ganen a `.fas`). Tras cambiar clases, regenerar con `.\scripts\build-css.ps1` o `scripts/build-css.sh` (`--watch` en desarrollo) y commitear `app.css`; `apps/public/tests/test_tailwind_build.py` falla si quedó desactualizado (se salta sin `npx`). No hay `package.json` a propósito: nixpacks detectaría Node y cambiaría el build de Railway
- **CDN con versión fija y SRI** en `base.html`: Font Awesome 6.5.0, HTMX 1.9.10, Alpine 3.17.5 (`integrity` + `crossorigin="anonymous"` + `referrerpolicy="no-referrer"`). Al actualizar una versión hay que recalcular el `integrity` (sha384) con los bytes exactos del archivo
- **Contraseñas**: todo `input type="password"` lleva `autocomplete` (`current-password` para la actual, `new-password` para crear o cambiar)
- **JS interactivo**: Alpine.js (directivas `x-data`, `x-model`, `x-show`)
- **Tema**: dark (`bg-zinc-900`/`bg-black`), color acento `#FF6B00` (`text-primary`, `bg-primary`)
- **Templates**: dos base templates — `base.html` (público y miembros) y `trainer/base.html` (panel admin)
- **Parciales reutilizables**: `templates/partials/` (navbar, footer, whatsapp_btn)
- **Context processor global**: `apps/public/context_processors.site_settings` inyecta `WHATSAPP_LINK`, `WHATSAPP_NUMBER`, `INSTAGRAM_URL` en todos los templates

### Pagos (dos sistemas independientes)

1. **Wompi** (`apps/payments`): pagos online con webhook. La vista `wompi_webhook` es `@csrf_exempt`. El `Payment` se crea al iniciar checkout; el webhook actualiza el status y activa la membresía.
2. **ManualPayment** (`apps/payments`): registrado por trainers para pagos en efectivo/transferencia. Solo visible a `is_superuser` en el nav.

### API Ruffier / Ruffier-Dickson

- Contrato, uso y decisiones: `docs/API_RUFFIER_DICKSON.md`.
- `GET/POST /api/v1/assessments/<assessment_pk>/ruffier-dickson/`: sesión trainer + asignación + salud habilitada + consentimiento vigente + adulto. CSRF obligatorio en POST.
- `CardioEvaluation` agrega mediciones con ambos índices y versiones; token UUID por valoración evita duplicados. Cálculo puro en `apps/assessments/cardio.py`. Sin interpretación clínica automática.
- `DixonTest` es el registro anterior: calcula Ruffier, no Dickson. Sus datos y escala heredada no se recalculan. La interfaz ahora aclara el nombre.
- El detalle de valoración usa `static/js/cardio-api.js` y `trainer/partials/cardio_api.html`. La exportación propia de salud incluye `cardio_evaluations`.

### Cálculos automáticos en save()

- `InitialAssessment.save()` → calcula y guarda `imc` e `imc_classification`
- `DixonTest.save()` → registro anterior: calcula `index_value` y `classification` de Ruffier (IR = (P0+P1+P2-200)/10).
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
- Menores de 18: no registran medidas, ni dan consentimiento propio, ni llenan la ficha. `apps.health.services.is_minor` usa la `birth_date` de la ficha vigente y, si todavía no hay ficha, la edad de la última valoración inicial.
- Las vistas de salud de la clienta responden `Cache-Control: no-store`, sin datos de salud en la URL ni en los logs.

### Ficha de salud y alimentación (fase A2 de `docs/contracts/plan-ia.md`)

- `HealthProfile`: una fila por versión (cada guardado crea una nueva; una sola `is_current` por clienta, restricción en la base). Listas cerradas en `apps/health/choices.py`; validación en `apps/health/forms.py` (`HealthProfileForm`, los 5 pasos del asistente); casos de uso en `apps/health/services.py` (`save_profile`, `confirm_profile`, `delete_profile`, `export_data`, `profile_flags`). Ni la ficha ni `HealthAccessLog` están en el admin.
- Rutas (`apps/health/urls.py`): la clienta usa `member_health_profile` (`/health/profile/`, `?edit=1` para editar), `..._confirm`, `..._export` (JSON adjunto) y `..._delete`, siempre sobre `request.user` (membresía vigente + `HEALTH_FEATURES_ENABLED`; apagado = 404). El entrenador usa `trainer_health_profile` y `trainer_health_profile_edit` (`get_client_for_trainer`; la edición también depende del interruptor).
- Guardar la ficha exige el consentimiento `health_data` vigente (no el de IA). El entrenador **solo corrige** una ficha existente (nunca la crea) y su versión queda sin confirmar hasta que la clienta la confirme. El entrenador solo ve el contenido con `health_data` vigente de la clienta.
- `HealthAccessLog` (sin IP ni contenido): `view`/`edit` del entrenador o la superusuaria, y `export`/`delete` de la propia clienta. Retirar el consentimiento `health_data` ofrece borrar la ficha.
- Comidas: `meal_slots` siempre incluye desayuno, almuerzo y cena; opcionalmente media mañana y/o merienda (3 a 5).
- Banderas (`apps/plans/rules/flags.py`, R01-R15, Y01-Y13, I01, I02, M01-M09): solo para el entrenador, nunca se muestran a la clienta. Umbrales **[A VALIDAR]** por un profesional de la salud.
- Las pantallas (`health/profile_*.html`, `trainer/health_profile*.html`) las construye el frontend contra el bloque "Contrato A2" de `docs/contracts/requests.md`; mientras no existan, las pruebas usan plantillas de reemplazo (`stub_missing_templates`).

### Planes con IA (fase B de `docs/contracts/plan-ia.md`: plan de ejercicio)

- Flujo: la clienta (o su entrenador) pide un plan -> `services.request_plan` evalúa con `services.assess` (consentimientos `health_data` + `ai_processing`, ficha confirmada, medida de menos de 30 días, textos legales sin marcadores, cupo, banderas rojas, catálogo filtrado) -> crea un `PlanGenerationJob` -> `jobs.start_job` lo corre en un hilo (cola en la base + recuperación de huérfanos, sin Celery) -> la salida de la IA se valida (`rules/validate.py`, un reintento con los códigos) -> se crea una `Routine` **inactiva** + `Plan` `draft_ai` -> el entrenador lo revisa (`trainer_plan_inbox`, `trainer_plan_detail`), lo edita en el constructor y lo aprueba (la rutina se activa y reemplaza al plan aprobado anterior).
- La IA **nunca decide**: con bandera roja o sin ejercicios compatibles (`D06`) no se llama; el trabajo queda `blocked` y se avisa al entrenador. A la IA solo van datos sin identificar (`jobs.build_exercise_payload`): sin nombre, correo, usuario, ids, fecha de nacimiento, diagnósticos ni texto libre de la ficha. Ni prompts ni respuestas se guardan en logs; la salida no validada no se guarda.
- La clienta nunca ve borradores: sus vistas filtran `is_active=True` (incluido `mark_day_complete`) y `member_plans` solo muestra `approved`. Los planes simulados (`is_simulated`) no se aprueban fuera de `DEBUG`.
- `Exercise` tiene etiquetas para el filtro (`intensity`, `equipment`, `risk_tags`); sin intensidad = "sin etiquetar" (no se propone a clientas con lesiones/advertencias ni en casa). `python manage.py list_untagged_exercises`.
- Rutas: clienta `member_plans` (`/plans/`), `member_plan_request`, `member_plan_request_status`, `member_plan_request_poll` (HTMX, responde 286 al terminar); entrenador `trainer_plan_inbox`, `trainer_plan_detail`, `trainer_plan_take/approve/regenerate/archive`, `trainer_plan_generate` (desde el detalle de la clienta), `trainer_plan_job` (+ `_poll`). Todas: 404 con la función apagada (`HEALTH_FEATURES_ENABLED` y `AI_PLANS_MODE`), `no-store`, autorización con `clients_for_trainer`.
- Comandos: `check_gemini` (prueba la clave con datos ficticios), `process_plan_jobs` (recupera trabajos huérfanos). `check_infra` revisa el modo de IA y los ejercicios sin etiquetar.
- Gemini por REST con la biblioteca estándar (`apps/plans/ai/gemini.py`, sin dependencias nuevas): clave en la cabecera `x-goog-api-key`, errores solo como códigos (`ai_http_429`...). Las pruebas usan `FakeAIClient` (`apps/plans/tests/helpers.py`), nunca la red.

### Plan de alimentación (fase C de `docs/contracts/plan-ia.md`)

Datos nutricionales USDA: `docs/INTEGRACION_DATOS_NUTRICIONALES.md`.
`fdc_search` solo consulta; `fdc_import` es vista previa salvo `--apply` con descripción confirmada.
Clave FDC_API_KEY solo en entorno; no imprimirla ni leer .env. Las importaciones quedan inactivas.
No mezclar 100 g y 100 ml. Nutrientes ausentes son null. Cada opción nueva congela fuentes y cantidades
en MealOption.nutrient_snapshot. `load_foods` no pisa alimentos vinculados a USDA.

**Actualización 2026-11-17:** el alcance actual es bienestar, con alimentación clínica pausada.
Ver `docs/MENUS_BIENESTAR_GEMINI.md` (prevalece sobre las referencias a adaptación clínica de esta sección).
`apps/plans/wellness.py` bloquea condiciones/medicamentos/embarazo/antecedentes alimentarios;
`rules/recipes.py` ofrece 117 plantillas cotidianas e internacionales (`everyday-world/2`) al simulador y a Gemini.
El backend valida selección exacta con V09_RECIPE y conserva preparación del recetario.
Variedad: la lista de recetas sale en un orden distinto por trabajo y comida; los nombres ya usados en el día se
excluyen de la lista y del esquema (con respaldo a la lista completa si no queda un grupo válido) y, si se puede,
las 3 opciones de una comida no repiten proteína principal (`main_protein`; V06). Ver `docs/MENUS_BIENESTAR_GEMINI.md`.
Prompts meal/5 y meal/6; las notas libres de alimentación no se envían al proveedor.

- Flujo: igual que el ejercicio (`services.request_plan(client, 'nutrition', ...)` -> trabajo -> hilo), pero `jobs._run_nutrition` hace **una llamada por comida** (`meal-slot/1`), valida cada una (`rules/meal_validate.py`, un reintento con códigos) y solo al final crea el `Plan` `draft_ai` con su `NutritionPlan` (objetivos calculados por Python: `rules/energy.py`), `MealSlot` y 3 `MealOption` activas por comida (`nutrition.create_nutrition_draft`). La IA **solo elige `food_id` y gramos**; kcal, macros y medidas caseras los calcula Python desde `Food`. Un fallo no guarda nada.
- Catálogo: `Food` (admin solo superusuaria). `python manage.py load_foods` carga ~90 alimentos con cifras **aproximadas y sin validar**; **fuera de `DEBUG` solo se usan los que tienen `validated_by`** (acción «Marcar como validados» del admin, la hace quien verifica las cifras). Sin alimentos suficientes tras el filtro (alergias, trazas, dieta, intolerancias, etiquetas de advertencias) -> `D06_FEW_FOODS` y el trabajo queda `blocked` sin llamar a la IA.
- A la IA nunca van alergias ni diagnósticos: se aplican al filtrar los alimentos; el texto libre que devuelve se revisa de nuevo con listas de palabras (`V03`).
- **Adaptación a la clienta** (`AI_PLANS_ADAPTATION`, apagada): `apps/plans/rules/adaptation.py` (puro) + `apps/plans/adaptation.py` (lectura de datos) agregan un bloque `history` al pedido a la IA y recortan lo que se le ofrece. Diseño y estado en `docs/contracts/plan-ia-adaptacion.md`.
- **Validación profesional SUSPENDIDA** (`AI_PLANS_REQUIRE_PROFESSIONAL`, por defecto `False`): hoy cualquier entrenador aprueba planes de alimentación y se usan los alimentos sin validar. Con `true` rige lo siguiente. `services.professional_required()` es el único interruptor.
- Con `AI_PLANS_REQUIRE_PROFESSIONAL=true`, aprobar un plan de alimentación exige `TrainerCredential.can_validate_nutrition` (la superusuaria la asigna en el admin; quién puede tenerla es una decisión legal) y que hoy no haya bandera roja ni alimentos incompatibles con la ficha. El entrenador edita opciones con `trainer_meal_option_edit` (solo alimentos permitidos a la clienta).
- La clienta (`member_nutrition_plan`, solo plan `approved` y suyo) elige una opción por comida y día (`MealChoice`, ventana de hoy + 6 días), puede pedir **«otra opción»** (`nutrition.request_alternative`: reemplaza una opción, queda `needs_review` para el entrenador, cupos por comida y por día) y ve intercambios y lista de compras. Opciones con un alimento que ya no le corresponde se marcan y no se pueden elegir.
- Pruebas: `test_nutrition_rules.py` (puras), `test_nutrition.py`, `test_nutrition_views.py`, `test_foods_admin.py`; el escenario `NutritionScenarioTestCase` (`tests/helpers.py`) carga el catálogo validado y da la credencial a la superusuaria.
- Marcar comidas (`member_meal_complete`, `POST /plans/nutrition/choices/<pk>/complete/`): la clienta marca «Cumplí esta comida» con `done=1` o `0` (idempotente; solo hoy o días anteriores y con opciones compatibles con su ficha; desmarcar siempre se permite). Es lo que escribe `MealChoice.completed_at`. La adaptación solo cuenta las comidas marcadas sobre opciones **activas** (lo que la clienta ve es lo que cuenta).
- Pedidos bloqueados por bandera roja (plan completo y «otra opción»): un solo trabajo y un solo aviso al entrenador por día, misma clienta, tipo y banderas (`services.recent_blocked_job`).

### Avisos por WhatsApp (`docs/contracts/whatsapp.md`)

- Avisos de una sola vía con plantillas *utility* (`apps/whatsapp/catalog.py`): membresía por vencer (3 días antes) y vencida, plan aprobado, "pendientes por revisar" para el entrenador (agrupado cada 3 h) y, opcional, pago recibido. **Nunca datos de salud**: solo primer nombre y una fecha. El correo sigue saliendo igual.
- Alta: `/accounts/whatsapp/` (`whatsapp_settings`, `whatsapp_enable`, `whatsapp_disable`, `whatsapp_resend_confirmation`), siempre sobre `request.user`. Consentimiento versionado `whatsapp_notices` (mismo `ConsentTextVersion`/`ConsentRecord`, pero las pantallas y reglas de salud usan `HEALTH_PURPOSES` y no lo ven) + confirmación del número con el botón "Sí, soy yo". Baja: web, botón "No recibir avisos" o BAJA/STOP. Menores de 18 no pueden activar.
- Cola en la base (`WhatsAppMessage`, idempotente por `dedupe_key`; solo ids, códigos y fechas): intento inmediato en hilo para avisos por acción y **cron de Railway** como respaldo: servicio aparte con `python manage.py process_whatsapp_queue` y Cron Schedule `*/15 12-23 * * 1-6` (UTC). Ventana Ley 2300 (L-V 7-19, sáb 8-15, sin domingos ni festivos de Colombia, `apps/whatsapp/calendar.py`). Errores de Meta por clase (`client/base.py`): de cuenta/token pausan el canal y avisan a la superusuaria; de plantilla pausan esa plantilla. `python manage.py check_whatsapp [--resume]`, `whatsapp_register_number`.
- Webhook `/whatsapp/webhook/` (`@csrf_exempt` solo ahí): firma `X-Hub-Signature-256` sobre el cuerpo crudo, cuenta y número propios, idempotente. Nunca se guarda ni registra el texto de los mensajes, teléfonos completos ni tokens.
- Pruebas sin red (`FakeWhatsAppClient`, `apps/whatsapp/tests/`). Pantallas: esqueletos `accounts/whatsapp_settings.html` y `trainer/whatsapp_settings.html` hasta que el frontend entregue las reales (`docs/contracts/requests.md`, "contrato W-01 a W-03").

### Desactivar y eliminar cuentas (`docs/contracts/cuentas-eliminar.md`)

- Tres estados: **activa**, **desactivada** (`is_active=False` + `deactivated_at`; reversible; no inicia sesión) y **eliminada** (lápida: la fila de `User` vaciada, `erased_at`, usuario `cuenta-eliminada-<pk>`, contraseña inutilizable). Todo pasa por `apps/accounts/services.py` (`deactivate_account`, `reactivate_account`, `erase_account`), en una transacción con `select_for_update()` y con la auditoría `AccountAuditLog` (solo ids, rol, acción, motivo de lista cerrada y conteos; admin de solo lectura para la superusuaria).
- Permisos: nunca sobre una superusuaria, ni sobre la propia cuenta, ni sobre una eliminada; siempre queda al menos una superusuaria activa. Un entrenador común **desactiva y reactiva** a sus clientas; **eliminar** es solo de la superusuaria, igual que todo lo de entrenadores. Al desactivar o eliminar un entrenador sus clientas pasan a `reassign_to` y sus planes `in_review` vuelven a la bandeja (`PlanEvent` `released`).
- Una clienta desactivada es de **solo lectura**: las vistas que escriben usan `permissions.reject_if_inactive` y los servicios `ensure_client_active`. `clients_for_trainer` excluye las lápidas (las desactivadas sí se ven, con `?status=inactive|all` en las listas).
- Eliminar borra los datos de la persona y conserva, sin datos personales y apuntando a la lápida, pagos (`ManualPayment.user` y `Payment.user` son `PROTECT`; `raw_webhook` queda en una lista blanca), comprobantes (bucket privado, solo la superusuaria, `receipt_retained_at`), consentimientos (retirados), registro de accesos a la ficha y auditoría. La foto se borra con `transaction.on_commit`. **Toda clave foránea nueva a `User` debe clasificarse en `services.ERASE_POLICY`** (una prueba falla si no).
- El admin de usuarios no borra (sin botón ni acción masiva) y deja `is_active`/`is_superuser` de solo lectura. `python manage.py purge_orphan_files [--delete]` cuenta o borra fotos y comprobantes que ninguna fila usa.
- Pantallas C-01 a C-08 entregadas (`docs/contracts/requests.md`); parciales comunes en `templates/trainer/partials/account_*.html` y `person_name.html`. Los errores que una plantilla pinta junto al campo van en las listas `*_RENDERED` de `apps/accounts/views.py` para no repetirlos arriba.
