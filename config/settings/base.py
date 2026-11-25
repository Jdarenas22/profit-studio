from pathlib import Path
import environ
from django.core.exceptions import ImproperlyConfigured as _ImproperlyConfigured

from config import security as _security

env = environ.Env()

BASE_DIR = Path(__file__).resolve().parent.parent.parent

environ.Env.read_env(BASE_DIR / '.env')

SECRET_KEY = env('SECRET_KEY', default='django-insecure-dev-key-change-in-production')
DEBUG = env.bool('DEBUG', default=False)
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=[])

# ─── Apps ──────────────────────────────────────────────────────────────────────
DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

THIRD_PARTY_APPS = [
    'django_htmx',
    'axes',
    'storages',
]

LOCAL_APPS = [
    'apps.accounts',
    'apps.memberships',
    'apps.assessments',
    'apps.health',
    'apps.plans',
    'apps.exercises',
    'apps.routines',
    'apps.payments',
    'apps.public',
    'apps.whatsapp',
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ─── Auth ──────────────────────────────────────────────────────────────────────
AUTH_USER_MODEL = 'accounts.User'

AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',
    'django.contrib.auth.backends.ModelBackend',
]

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/accounts/dashboard/'
LOGOUT_REDIRECT_URL = '/'

# ─── Middleware ─────────────────────────────────────────────────────────────────
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'config.middleware.PermissionsPolicyMiddleware',   # antes de WhiteNoise: también cubre los estáticos
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django_htmx.middleware.HtmxMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'axes.middleware.AxesMiddleware',
]

ROOT_URLCONF = 'config.urls'
CSRF_FAILURE_VIEW = 'apps.assessments.api.csrf_failure'

# ─── Permissions-Policy (config/middleware.py) ─────────────────────────────────
# Funciones del navegador que la web NO usa: se niegan a la página y a todo lo que incruste. Una lista
# vacía = nadie. NO se niegan las que usa el video de YouTube incrustado (templates/exercises/detail.html:
# accelerometer, autoplay, clipboard-write, encrypted-media, gyroscope, picture-in-picture) ni fullscreen;
# una prueba (apps/public/tests/test_permissions_policy.py) compara esta lista con los `allow="..."` de
# las plantillas. `payment`: el widget de Wompi (https://checkout.wompi.co/widget.js) abre su checkout en
# un iframe sin `allow` (verificado el 2026-11-20), así que hoy no lo necesita; se deja a su origen por si
# algún día activa Apple Pay/Google Pay (negarlo en silencio quitaría un medio de pago).
PERMISSIONS_POLICY = {
    'camera': [],
    'microphone': [],
    'geolocation': [],
    'payment': ['self', 'https://checkout.wompi.co'],
    'usb': [],
    'serial': [],
    'bluetooth': [],
    'hid': [],
    'midi': [],
    'display-capture': [],
    'magnetometer': [],
    'xr-spatial-tracking': [],
}

# ─── Templates ─────────────────────────────────────────────────────────────────
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.public.context_processors.site_settings',
                'apps.health.context_processors.health_flags',
                'apps.plans.context_processors.plan_flags',
                'apps.whatsapp.context_processors.whatsapp_flags',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# ─── Password validation ────────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ─── Internacionalización ───────────────────────────────────────────────────────
LANGUAGE_CODE = 'es-co'
TIME_ZONE = 'America/Bogota'
USE_I18N = True
USE_TZ = True

# ─── Archivos estáticos ─────────────────────────────────────────────────────────
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

# ─── Almacenamiento (STORAGES) ──────────────────────────────────────────────────
# Django 5.1 eliminó DEFAULT_FILE_STORAGE y STATICFILES_STORAGE: ya no se leen y no avisan.
# 'default' guarda las subidas (media); 'staticfiles' sirve los estáticos con WhiteNoise, con huella en
# el nombre (manifest; production.py lo repite y explica). Con R2, production.py cambia también 'default'.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}

# ─── Archivos media (subidas por usuarios) ───────────────────────────────────────
# Producción sin R2 usa estas mismas rutas; con R2 las sobreescribe production.py.
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ─── Brute force protection (django-axes) ──────────────────────────────────────
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1  # horas
AXES_LOCKOUT_TEMPLATE = 'accounts/lockout.html'
# Bloqueo por combinación usuario + IP: un atacante no puede dejar fuera al usuario
# legítimo (que entra desde otra IP) ni bloquear a todos los visitantes a la vez.
AXES_LOCKOUT_PARAMETERS = [['username', 'ip_address']]
AXES_RESET_ON_SUCCESS = True

# ─── Antirrobots opcional: Cloudflare Turnstile (docs/contracts/verificacion-correo.md, sección 8) ──────────
# Se exige y se muestra en el registro y en «Reenviar el correo» SOLO si existen las dos variables. La
# secreta se usa únicamente en el servidor (apps/accounts/turnstile.py) y nunca va a una plantilla ni a un log.
TURNSTILE_SITE_KEY = env('TURNSTILE_SITE_KEY', default='')
TURNSTILE_SECRET_KEY = env('TURNSTILE_SECRET_KEY', default='')

# ─── Contacto ProFit Studio ────────────────────────────────────────────────────
WHATSAPP_NUMBER = env('WHATSAPP_NUMBER', default='+573005638196')
WHATSAPP_LINK = env('WHATSAPP_LINK', default='https://wa.me/573005638196')
INSTAGRAM_URL = env('INSTAGRAM_URL', default='https://instagram.com/profitstudio75')

# ─── Wompi — Pasarela de pagos Colombia ────────────────────────────────────────
# Crea tu cuenta en https://comercios.wompi.co y obtén tus claves
WOMPI_PUBLIC_KEY = env('WOMPI_PUBLIC_KEY', default='')
WOMPI_PRIVATE_KEY = env('WOMPI_PRIVATE_KEY', default='')
WOMPI_INTEGRITY_SECRET = env('WOMPI_INTEGRITY_SECRET', default='')
WOMPI_EVENTS_SECRET = env('WOMPI_EVENTS_SECRET', default='')

# ─── Salud de la clienta (medidas, consentimiento, ficha) ─────────────────────
# Interruptor de las pantallas de salud de la clienta. APAGADO por defecto (producción): se
# enciende cuando un abogado aprobó los textos de consentimiento y la superusuaria publicó la
# versión definitiva (Admin > Textos de consentimiento). development.py lo enciende.
HEALTH_FEATURES_ENABLED = env.bool('HEALTH_FEATURES_ENABLED', default=False)
# URL pública de la política de tratamiento de datos personales (vacía = no se muestra el enlace).
PRIVACY_POLICY_URL = env('PRIVACY_POLICY_URL', default='')

# ─── Planes con IA (fase B de docs/contracts/plan-ia.md) ──────────────────────
# AI_PLANS_MODE: off | live. simulated se reserva para las pruebas automatizadas.
# Free envía únicamente catálogo y requisitos de estructura; la ficha se procesa en Django.
AI_PLANS_MODE = env('AI_PLANS_MODE', default='')
GEMINI_API_KEY = env('GEMINI_API_KEY', default='')            # solo entorno; nunca en código ni logs
FDC_API_KEY = env('FDC_API_KEY', default='')
# Tiempo máximo de una conexión de correo (SMTP o API): sin él un servidor mudo bloquea el proceso hasta que
# gunicorn lo mata (WORKER TIMEOUT).
EMAIL_TIMEOUT = env.int('EMAIL_TIMEOUT', default=10)

FDC_TIMEOUT_SECONDS = env.int('FDC_TIMEOUT_SECONDS', default=15)
NUTRITION_REQUIRE_USDA_REFERENCE = env.bool('NUTRITION_REQUIRE_USDA_REFERENCE', default=True)
GEMINI_MODEL = env('GEMINI_MODEL', default='')                # p. ej. un modelo "flash-lite" estable
GEMINI_BILLING_CONFIRMED = env.bool('GEMINI_BILLING_CONFIRMED', default=False)
GEMINI_ACCESS_TIER = env('GEMINI_ACCESS_TIER', default='free')
GEMINI_FREE_TIER_CONFIRMED = env.bool('GEMINI_FREE_TIER_CONFIRMED', default=False)
GEMINI_MAX_DAILY_CALLS = env.int('GEMINI_MAX_DAILY_CALLS', default=30)
GEMINI_TIMEOUT_SECONDS = env.int('GEMINI_TIMEOUT_SECONDS', default=60)
# Campo del esquema de salida en generationConfig: responseJsonSchema | none (ver apps/plans/ai/gemini.py)
GEMINI_SCHEMA_FIELD = env('GEMINI_SCHEMA_FIELD', default='responseJsonSchema')
# Límites de uso (control de costos, contados en la base de datos)
AI_PLANS_MAX_ATTEMPTS = env.int('AI_PLANS_MAX_ATTEMPTS', default=3)
AI_PLANS_DAILY_JOB_LIMIT = env.int('AI_PLANS_DAILY_JOB_LIMIT', default=100)
AI_PLANS_CLIENT_WEEKLY_LIMIT = env.int('AI_PLANS_CLIENT_WEEKLY_LIMIT', default=3)
AI_PLANS_TRAINER_DAILY_LIMIT = env.int('AI_PLANS_TRAINER_DAILY_LIMIT', default=10)
# "Otra opción" de una comida (fase C): por comida y semana, y por clienta y día
AI_PLANS_ALT_SLOT_WEEKLY_LIMIT = env.int('AI_PLANS_ALT_SLOT_WEEKLY_LIMIT', default=3)
AI_PLANS_ALT_DAILY_LIMIT = env.int('AI_PLANS_ALT_DAILY_LIMIT', default=6)
# Validación profesional de los planes. SUSPENDIDA por decisión del negocio (False): cualquier entrenador
# aprueba planes de alimentación y se usan todos los alimentos del catálogo aunque no estén validados.
# True = se exige la credencial de nutrición (TrainerCredential.can_validate_nutrition) para aprobar
# y fuera de DEBUG solo cuentan los alimentos con `validated_by`.
AI_PLANS_REQUIRE_PROFESSIONAL = env.bool('AI_PLANS_REQUIRE_PROFESSIONAL', default=False)
# Adaptación a la clienta (docs/contracts/plan-ia-adaptacion.md): agrega al pedido a la IA un resumen de su
# historial. APAGADA por defecto. No la enciendas hasta tener el texto de consentimiento aprobado por el
# abogado y los prompts de adaptación (paso 4): el interruptor solo mete el bloque `history` en el pedido.
AI_PLANS_ADAPTATION = env.bool('AI_PLANS_ADAPTATION', default=False)
# Ejecutar el trabajo en la misma petición, sin hilo (pruebas y depuración)
AI_PLANS_RUN_INLINE = env.bool('AI_PLANS_RUN_INLINE', default=False)
# Modo trabajador (APAGADO por defecto). False = los trabajos de IA corren en un hilo del proceso web
# (como siempre). True = la web NO lanza hilos: deja el trabajo `queued` y lo toma un servicio aparte con
# `python manage.py process_plan_jobs --loop`. Ese servicio consulta la base cada pocos segundos, así que
# mantiene despierto a Postgres y gasta crédito las 24 horas: es una decisión del dueño, no usarlo en el plan
# gratuito (docs/DEPLOY.md, sección 12). AI_PLANS_RUN_INLINE (pruebas) tiene prioridad. Booleano estricto:
# un valor que no sea true/false detiene el arranque (como SECURITY_STRICT_SECRETS).
try:
    AI_PLANS_WORKER = _security.parse_bool_flag('AI_PLANS_WORKER', env('AI_PLANS_WORKER', default=''))
except ValueError as _exc:
    raise _ImproperlyConfigured(str(_exc)) from None

# ─── Avisos por WhatsApp (docs/contracts/whatsapp.md) ─────────────────────────
# WhatsApp Business Cloud API de Meta. NO confundir con WHATSAPP_NUMBER / WHATSAPP_LINK (botón wa.me
# de soporte). WHATSAPP_CLOUD_MODE: off | simulated | live. Vacío = simulated con DEBUG y off sin él.
# En live, production.py no arranca sin las variables obligatorias (sección 8 del contrato).
WHATSAPP_CLOUD_MODE = env('WHATSAPP_CLOUD_MODE', default='')
WHATSAPP_CLOUD_ACCESS_TOKEN = env('WHATSAPP_CLOUD_ACCESS_TOKEN', default='')   # solo entorno; nunca en logs
WHATSAPP_CLOUD_PHONE_NUMBER_ID = env('WHATSAPP_CLOUD_PHONE_NUMBER_ID', default='')
WHATSAPP_CLOUD_WABA_ID = env('WHATSAPP_CLOUD_WABA_ID', default='')
WHATSAPP_CLOUD_APP_SECRET = env('WHATSAPP_CLOUD_APP_SECRET', default='')
WHATSAPP_CLOUD_VERIFY_TOKEN = env('WHATSAPP_CLOUD_VERIFY_TOKEN', default='')
WHATSAPP_CLOUD_GRAPH_VERSION = env('WHATSAPP_CLOUD_GRAPH_VERSION', default='v27.0')
WHATSAPP_CLOUD_TEMPLATE_LANG = env('WHATSAPP_CLOUD_TEMPLATE_LANG', default='es_CO')
WHATSAPP_CLOUD_TIMEOUT_SECONDS = env.int('WHATSAPP_CLOUD_TIMEOUT_SECONDS', default=10)
WHATSAPP_CLOUD_DAILY_LIMIT = env.int('WHATSAPP_CLOUD_DAILY_LIMIT', default=200)
WHATSAPP_CLOUD_EXPIRING_DAYS = env.int('WHATSAPP_CLOUD_EXPIRING_DAYS', default=3)
WHATSAPP_CLOUD_PAYMENT_NOTICE = env.bool('WHATSAPP_CLOUD_PAYMENT_NOTICE', default=False)
WHATSAPP_CLOUD_SEND_INLINE = env.bool('WHATSAPP_CLOUD_SEND_INLINE', default=False)
# Pendientes legales (pregunta 14 del contrato). En live NO se envía nada (ni se ofrece activar) mientras
# falte alguno: dominio definitivo (https, se usa en los textos), revisión del abogado confirmada y
# PRIVACY_POLICY_URL (arriba). Además el texto de consentimiento vigente no debe tener marcadores [...].
WHATSAPP_CLOUD_SITE_URL = env('WHATSAPP_CLOUD_SITE_URL', default='')
WHATSAPP_CLOUD_LEGAL_REVIEWED = env.bool('WHATSAPP_CLOUD_LEGAL_REVIEWED', default=False)
