import re
import warnings
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

from config import security as _security

from .base import *

DEBUG = False

# ─── Modo estricto de secretos (gradual) ───────────────────────────────────────
# SECURITY_STRICT_SECRETS=true convierte en error de arranque lo que por defecto solo se
# avisa: una SECRET_KEY que Django marcaría con security.W009 y, con R2, un bucket de
# comprobantes ausente o igual al de media. Activarla en Railway solo DESPUÉS de confirmar
# ambas cosas (ver config/security.py). Un valor que no sea true/false detiene el arranque.
try:
    SECURITY_STRICT_SECRETS = _security.parse_strict_flag(env('SECURITY_STRICT_SECRETS', default=''))
except ValueError as _exc:
    raise ImproperlyConfigured(str(_exc)) from None

# ─── Variables obligatorias: el servidor NO arranca si faltan o son inseguras ───
# SECRET_KEY: sin valor por defecto. Se rechazan el valor de desarrollo de base.py
# y el de ejemplo de .env.example.
SECRET_KEY = env('SECRET_KEY', default='')
if _security.secret_key_is_placeholder(SECRET_KEY):
    raise ImproperlyConfigured(
        'SECRET_KEY es obligatoria en producción: defínela como variable de entorno '
        '(Railway > Variables) con un valor largo y aleatorio; no sirven el valor por '
        'defecto ni el de .env.example.'
    )
# Fuerza de la clave (criterios de security.W009). Por defecto solo se avisa con el
# chequeo profit.W001 (check --deploy) y en check_infra; en modo estricto no arranca.
# El mensaje nombra la variable y el motivo, nunca el valor.
_secret_weaknesses = _security.secret_key_weaknesses(SECRET_KEY)
if _secret_weaknesses and SECURITY_STRICT_SECRETS:
    raise ImproperlyConfigured(
        'SECRET_KEY no cumple los criterios de seguridad de Django (security.W009): '
        + '; '.join(_secret_weaknesses) + '. Con SECURITY_STRICT_SECRETS=true el servidor no '
        'arranca. ' + _security.SECRET_KEY_ACTION_TEXT
    )

# ALLOWED_HOSTS: obligatorio y sin comodín. Ejemplo:
#   ALLOWED_HOSTS=profit-studio.up.railway.app,tudominio.com
ALLOWED_HOSTS = [h.strip() for h in env.list('ALLOWED_HOSTS', default=[]) if h.strip()]
if not ALLOWED_HOSTS or '*' in ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        'ALLOWED_HOSTS es obligatorio en producción y no puede contener "*". '
        'Ejemplo: ALLOWED_HOSTS=profit-studio.up.railway.app,tudominio.com'
    )

# Railway inyecta RAILWAY_PUBLIC_DOMAIN con el dominio público del servicio.
_railway_domain = env('RAILWAY_PUBLIC_DOMAIN', default='').strip()
if _railway_domain and _railway_domain not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_railway_domain)

# Orígenes de confianza para CSRF (formularios por HTTPS): los dominios declarados.
# Un host que empieza con "." (subdominios) se traduce a "https://*.dominio".
CSRF_TRUSTED_ORIGINS = [
    f'https://*{host}' if host.startswith('.') else f'https://{host}'
    for host in ALLOWED_HOSTS
]

# El healthcheck de Railway llama con el host healthcheck.railway.app: se añade
# siempre a ALLOWED_HOSTS (después de calcular CSRF, no necesita origen de confianza)
# para que un ALLOWED_HOSTS bien configurado nunca rompa el despliegue.
if 'healthcheck.railway.app' not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append('healthcheck.railway.app')

# ─── Planes con IA: modo live solo con la configuración completa ───────────────
_ai_mode = (AI_PLANS_MODE or '').strip().lower()
if _ai_mode not in ('', 'off', 'simulated', 'live'):
    raise ImproperlyConfigured('AI_PLANS_MODE debe ser off, simulated o live.')
if _ai_mode == 'live':
    from types import SimpleNamespace
    from apps.plans.ai.policy import configuration_errors
    _ai_missing = configuration_errors(SimpleNamespace(**globals()))
    if _ai_missing:
        raise ImproperlyConfigured('AI_PLANS_MODE=live exige ' + ', '.join(_ai_missing) + '.')
if GEMINI_SCHEMA_FIELD not in ('responseJsonSchema', 'none'):
    raise ImproperlyConfigured('GEMINI_SCHEMA_FIELD debe ser responseJsonSchema o none.')

# ─── Avisos por WhatsApp: live solo con la configuración completa ──────────────
# (docs/contracts/whatsapp.md, sección 8). El mensaje nombra la variable, nunca su valor. Los pendientes
# legales (dominio, política, revisión del abogado, texto sin marcadores) NO detienen el arranque: con
# ellos pendientes el sistema simplemente no ofrece activar ni envía nada (apps.whatsapp.conf).
_wa_mode = (WHATSAPP_CLOUD_MODE or '').strip().lower()
if _wa_mode not in ('', 'off', 'simulated', 'live'):
    raise ImproperlyConfigured('WHATSAPP_CLOUD_MODE debe ser off, simulated o live.')
if _wa_mode == 'live':
    # Misma regla que apps.whatsapp.conf.missing_live_settings (aquí no se puede importar: los settings
    # aún se están cargando).
    _wa_missing = [name for name, value in (
        ('WHATSAPP_CLOUD_ACCESS_TOKEN', WHATSAPP_CLOUD_ACCESS_TOKEN),
        ('WHATSAPP_CLOUD_PHONE_NUMBER_ID', WHATSAPP_CLOUD_PHONE_NUMBER_ID),
        ('WHATSAPP_CLOUD_WABA_ID', WHATSAPP_CLOUD_WABA_ID),
        ('WHATSAPP_CLOUD_APP_SECRET', WHATSAPP_CLOUD_APP_SECRET),
        ('WHATSAPP_CLOUD_VERIFY_TOKEN', WHATSAPP_CLOUD_VERIFY_TOKEN),
        ('WHATSAPP_CLOUD_GRAPH_VERSION', WHATSAPP_CLOUD_GRAPH_VERSION),
    ) if not str(value or '').strip()]
    if WHATSAPP_CLOUD_VERIFY_TOKEN and len(WHATSAPP_CLOUD_VERIFY_TOKEN) < 32:
        _wa_missing.append('WHATSAPP_CLOUD_VERIFY_TOKEN (mínimo 32 caracteres)')
    if WHATSAPP_CLOUD_GRAPH_VERSION and not re.match(r'^v\d+\.\d+$', WHATSAPP_CLOUD_GRAPH_VERSION):
        _wa_missing.append('WHATSAPP_CLOUD_GRAPH_VERSION (formato vNN.N)')
    if _wa_missing:
        raise ImproperlyConfigured('WHATSAPP_CLOUD_MODE=live exige ' + ', '.join(_wa_missing) + '.')

# ─── django-axes detrás del proxy de Railway ───────────────────────────────────
# Railway envía la IP real del cliente en X-Real-IP. Sin esto, todos los visitantes
# compartirían la IP del proxy. django-axes 6.5.0 solo entiende las opciones de proxy
# (AXES_IPWARE_*) si está instalado django-ipware (no lo está): se usa una función propia.
AXES_CLIENT_IP_CALLABLE = 'apps.accounts.axes_utils.get_client_ip'

# Necesario para que HTTPS funcione correctamente detrás del proxy de Railway
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ─── Base de datos ──────────────────────────────────────────────────────────────
# DATABASE_URL se inyecta cuando conectas PostgreSQL en Railway.
# Sin ella usa SQLite como fallback temporal.
DATABASES = {
    'default': env.db('DATABASE_URL', default='sqlite:///db.sqlite3')
}

# ─── Conexiones a la base de datos (persistentes solo si se pide) ──────────────
# Con CONN_MAX_AGE=0 (el valor por defecto de Django y de aquí) Django abre una conexión nueva a PostgreSQL
# en CADA petición y la cierra al terminarla: TCP + TLS + autenticación, ~200-250 ms medidos en Railway (el
# 2026-11-21 la portada tardaba ~585 ms con base de datos y ~225-340 ms sin ella). Con CONN_MAX_AGE > 0 cada
# worker de gunicorn reutiliza su conexión mientras tenga menos de esos segundos de vida: se comprueba al
# empezar y al terminar cada petición, así que la última conexión queda abierta hasta la siguiente visita.
# POR DEFECTO ES 0 A PROPÓSITO: Railway está en plan gratuito con App Sleeping encendido, y su documentación
# dice que las conexiones abiertas a la base de datos cuentan como actividad y evitan que el servicio se
# duerma (docs/DEPLOY.md, sección 11). Al pasar a un plan de pago (o si se prefiere un servicio siempre
# despierto y más rápido), se activa con DB_CONN_MAX_AGE=60.
# CONN_HEALTH_CHECKS=True hace un `SELECT 1` antes de la primera consulta de cada petición que reutiliza la
# conexión: si Postgres se reinició o se durmió, la conexión muerta se descarta y se abre otra en vez de
# responder 500. Con CONN_MAX_AGE=0 no hace nada (la conexión se cierra en cada petición).
#   DB_CONN_MAX_AGE=<segundos>  entero >= 0. Sin definir (o vacía): 0 = una conexión nueva por petición.
#                               Un valor inválido detiene el arranque (como SECURITY_STRICT_SECRETS o
#                               AI_PLANS_MODE): Railway no da por bueno el despliegue y conserva el anterior,
#                               en vez de ignorar en silencio lo que se pidió.
# DB_CONN_MAX_AGE es la ÚNICA perilla: si DATABASE_URL trae ?conn_max_age=... o ?conn_health_checks=... se
# sobrescriben (django-environ entrega 'false' como texto, y un texto cualquiera cuenta como verdadero
# para Django). El resto de lo que da env.db (motor, host, OPTIONS...) no se toca. Los hilos de IA y
# WhatsApp cierran su conexión al terminar (apps/plans/jobs.py, apps/whatsapp/queue.py) y los comandos
# de gestión terminan con el proceso.
# Cuántas conexiones puede haber a la vez: docs/DEPLOY.md, sección 11.
_DB_CONN_MAX_AGE_DEFAULT = 0
_db_conn_max_age = env('DB_CONN_MAX_AGE', default='').strip()
if _db_conn_max_age:
    if not re.fullmatch(r'[0-9]+', _db_conn_max_age):
        raise ImproperlyConfigured(
            'DB_CONN_MAX_AGE debe ser un número entero de segundos, 0 o mayor (0 = una conexión nueva '
            'por petición, el valor por defecto; 60 = reutilizarla un minuto). Quita la variable para '
            'usar el valor por defecto.'
        )
    DATABASES['default']['CONN_MAX_AGE'] = int(_db_conn_max_age)
else:
    DATABASES['default']['CONN_MAX_AGE'] = _DB_CONN_MAX_AGE_DEFAULT
DATABASES['default']['CONN_HEALTH_CHECKS'] = True

# ─── Seguridad ──────────────────────────────────────────────────────────────────
# SECURE_SSL_REDIRECT = False porque Railway maneja el redireccionamiento HTTP→HTTPS
# en su propio proxy/load balancer. Si Django también lo hace, el healthcheck de
# Railway (que va por HTTP directo al contenedor) recibe un 301 y falla.
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

# ─── Archivos estáticos — con huella en el nombre (manifest) ───────────────────
# CompressedManifestStaticFilesStorage comprime y renombra cada archivo con una huella de su contenido
# (app.css -> app.<12 hex>.css) y guarda la correspondencia en staticfiles/staticfiles.json. WhiteNoise
# sirve los nombres con huella con `Cache-Control: max-age=315360000, public, immutable` (diez años) y los
# nombres sin huella con caché corta (60 s): al cambiar un archivo cambia su URL, así que nadie queda
# con una copia vieja ni hace falta esperar a que caduque.
# Contrapartida: `collectstatic` TIENE que haber corrido (en el build de Railway; start.sh lo repite si
# falta el manifest y NO arranca si falla). Con el manifest ausente, o si una plantilla pide con
# {% static %} un archivo que no existe, Django lanza ValueError y la página responde 500: por eso
# apps/public/tests/test_static_references.py revisa todas las plantillas. No se usa
# WHITENOISE_MANIFEST_STRICT=False: taparía el error en vez de avisarlo.
STORAGES = {
    **STORAGES,
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}

# ─── Cloudflare R2 — almacenamiento de archivos (OPCIONAL) ─────────────────────
_r2_key       = env('R2_ACCESS_KEY_ID', default='')
_r2_secret    = env('R2_SECRET_ACCESS_KEY', default='')
_r2_bucket    = env('R2_BUCKET_NAME', default='')
_r2_endpoint  = env('R2_ENDPOINT_URL', default='')   # API de boto3: https://<account-id>.r2.cloudflarestorage.com
_r2_public    = env('R2_PUBLIC_URL', default='')      # URL pública del bucket: https://pub-xxxx.r2.dev
# Bucket PRIVADO solo para comprobantes de pago (receipts/). Ver apps/payments/storage.py.
_r2_receipts  = env('R2_RECEIPTS_BUCKET_NAME', default='')

if _r2_key and _r2_secret and _r2_bucket and _r2_endpoint:
    AWS_ACCESS_KEY_ID       = _r2_key
    AWS_SECRET_ACCESS_KEY   = _r2_secret
    AWS_STORAGE_BUCKET_NAME = _r2_bucket
    AWS_S3_ENDPOINT_URL     = _r2_endpoint  # Usado por boto3 para subir archivos
    AWS_S3_REGION_NAME      = 'auto'         # R2 no tiene regiones; "auto" es lo que documenta Cloudflare
    AWS_S3_SIGNATURE_VERSION = 's3v4'        # R2 solo acepta firmas SigV4 (las URLs firmadas lo necesitan)
    AWS_DEFAULT_ACL         = None           # R2 no soporta ACLs por objeto; acceso controlado a nivel bucket
    AWS_S3_FILE_OVERWRITE   = False
    AWS_QUERYSTRING_AUTH    = False          # media pública: URLs permanentes (requiere bucket público en Cloudflare)
    # La media pública va a R2 (los estáticos siguen con WhiteNoise: ver STORAGES más arriba)
    STORAGES = {
        **STORAGES,
        'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
    }

    # ── URLs de la media pública ──────────────────────────────────────────────────
    # django-storages (S3Boto3Storage.url) NO usa MEDIA_URL: arma `FieldFile.url` con
    # AWS_S3_CUSTOM_DOMAIN si existe ("<protocolo>//<dominio>/<ruta del archivo>", sin
    # añadir el bucket) y, si no, con el endpoint de la API (<cuenta>.r2.cloudflarestorage.com/
    # <bucket>/...), que exige credenciales y no se puede abrir desde el navegador.
    # Por eso, con R2_PUBLIC_URL (ej.: https://pub-abc123.r2.dev, sin el bucket en la ruta)
    # se toma de ahí solo el host (y puerto) como AWS_S3_CUSTOM_DOMAIN.
    # R2_PUBLIC_URL debe ser https y no llevar ruta: r2.dev y los dominios personalizados de
    # R2 sirven el bucket en la raíz; una ruta (típicamente el nombre del bucket) daría 404.
    # Si se define mal, el servidor no arranca con un mensaje claro en vez de publicar
    # enlaces rotos. Sin R2_PUBLIC_URL se mantiene el comportamiento anterior (endpoint de API).
    # Los comprobantes NO heredan esto (ver más abajo y apps/payments/storage.py).
    if _r2_public:
        _public = urlsplit(_r2_public)
        try:
            _public_port = _public.port
        except ValueError:
            _public_port = -1
        if (
            _public.scheme != 'https' or not _public.hostname or _public_port == -1
            or _public.path not in ('', '/') or _public.query or _public.fragment
            or _public.username or _public.password
        ):
            raise ImproperlyConfigured(
                'R2_PUBLIC_URL debe ser solo el origen público del bucket, con https y sin ruta, '
                'ej.: https://pub-abc123.r2.dev (sin el nombre del bucket al final).'
            )
        AWS_S3_CUSTOM_DOMAIN = _public.hostname + (f':{_public_port}' if _public_port else '')
        AWS_S3_URL_PROTOCOL  = 'https:'
        MEDIA_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/'
    else:
        MEDIA_URL = f'{_r2_endpoint}/{_r2_bucket}/'   # fallback: endpoint de API (no público)

    # ── Comprobantes de pago: NUNCA públicos ──────────────────────────────────────
    # En R2 "público" (r2.dev o dominio personalizado) se activa por BUCKET completo, no por
    # prefijo: si receipts/ vive en el bucket público de media, cualquiera que tenga la URL
    # (https://pub-xxxx.r2.dev/receipts/<id>.png) ve el comprobante. Por eso los comprobantes
    # usan su propio storage (apps/payments/storage.py): sin dominio personalizado, URLs
    # firmadas de 5 minutos, y bucket aparte si se define R2_RECEIPTS_BUCKET_NAME.
    # receipts_storage() pasa custom_domain=None y querystring_auth=True de forma explícita,
    # así que AWS_S3_CUSTOM_DOMAIN y AWS_QUERYSTRING_AUTH=False de arriba no le aplican.
    # La app los entrega solo por payments/receipts/<pk>/ (la vista lee con las credenciales
    # del servidor y comprueba quién pide el archivo).
    # MEDIA_URL / R2_PUBLIC_URL solo alimentan el storage de media pública; los comprobantes
    # no los usan.
    # Sin bucket propio (o con el mismo de media): por defecto solo se avisa (aquí y con el
    # chequeo profit.W002); con SECURITY_STRICT_SECRETS=true el servidor no arranca.
    RECEIPTS_BUCKET_NAME = _r2_receipts
    _receipts_problem = _security.receipts_bucket_problem(_r2_bucket, _r2_receipts)
    if _receipts_problem:
        _receipts_message = (_security.RECEIPTS_PROBLEM_TEXT[_receipts_problem] + ' '
                             + _security.RECEIPTS_RISK_TEXT + ' ' + _security.RECEIPTS_ACTION_TEXT)
        if SECURITY_STRICT_SECRETS:
            raise ImproperlyConfigured(
                _receipts_message + ' Con SECURITY_STRICT_SECRETS=true el servidor no arranca.')
        warnings.warn(_receipts_message, RuntimeWarning, stacklevel=2)
else:
    MEDIA_ROOT = BASE_DIR / 'media'
    MEDIA_URL  = '/media/'
    RECEIPTS_BUCKET_NAME = ''

# ─── Cache y sesiones (OPCIONAL con Redis) ──────────────────────────────────────
_redis_url = env('REDIS_URL', default='')

if _redis_url:
    CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': _redis_url,
            'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient'},
        }
    }
    SESSION_ENGINE      = 'django.contrib.sessions.backends.cache'
    SESSION_CACHE_ALIAS = 'default'
else:
    CACHES = {
        'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'},
    }

# ─── Sesión — expira al cerrar el navegador ─────────────────────────────────────
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 43200  # 12 horas máximo aunque no cierre el navegador

# ─── Email — contraseña olvidada ────────────────────────────────────────────────
_email_user = env('EMAIL_HOST_USER', default='')
if _email_user:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = env('EMAIL_HOST', default='smtp.gmail.com')
    EMAIL_PORT = env.int('EMAIL_PORT', default=587)
    EMAIL_USE_TLS = True
    EMAIL_HOST_USER = _email_user
    EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
    DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default=f'ProFit Studio <{_email_user}>')
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

PASSWORD_RESET_TIMEOUT = 3600  # 1 hora para usar el enlace de reseteo

# ─── Logging ────────────────────────────────────────────────────────────────────
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {'class': 'logging.StreamHandler'},
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
    'loggers': {
        'apps.payments': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        # Planes con IA: solo ids, estados, tokens y códigos de error (nunca datos de salud)
        'apps.plans': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        # Avisos por WhatsApp: ids, tipos, estados y códigos (nunca teléfonos, tokens ni textos)
        'apps.whatsapp': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
