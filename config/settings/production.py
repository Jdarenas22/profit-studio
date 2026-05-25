from .base import *

DEBUG = False

# ALLOWED_HOSTS — define ALLOWED_HOSTS en Railway con tu dominio real
# Ejemplo: ALLOWED_HOSTS=profit-studio.up.railway.app,tudominio.com
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['*'])

# Cabecera que Railway inyecta para indicar el host original
CSRF_TRUSTED_ORIGINS = [
    f'https://{host}' for host in ALLOWED_HOSTS if host != '*'
] or ['https://*.up.railway.app']

# Necesario para que HTTPS funcione correctamente detrás del proxy de Railway
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ─── Base de datos ──────────────────────────────────────────────────────────────
# DATABASE_URL se inyecta cuando conectas PostgreSQL en Railway.
# Sin ella usa SQLite como fallback temporal.
DATABASES = {
    'default': env.db('DATABASE_URL', default='sqlite:///db.sqlite3')
}

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

# ─── Archivos estáticos — sin manifest para evitar errores al iniciar ──────────
# CompressedStaticFilesStorage comprime los archivos pero NO crea staticfiles.json
# Evita el error "Missing staticfiles.json manifest" si collectstatic falla.
STATICFILES_STORAGE = 'whitenoise.storage.CompressedStaticFilesStorage'

# ─── Cloudflare R2 — almacenamiento de archivos (OPCIONAL) ─────────────────────
_r2_key      = env('R2_ACCESS_KEY_ID', default='')
_r2_secret   = env('R2_SECRET_ACCESS_KEY', default='')
_r2_bucket   = env('R2_BUCKET_NAME', default='')
_r2_endpoint = env('R2_ENDPOINT_URL', default='')

if _r2_key and _r2_secret and _r2_bucket and _r2_endpoint:
    AWS_ACCESS_KEY_ID       = _r2_key
    AWS_SECRET_ACCESS_KEY   = _r2_secret
    AWS_STORAGE_BUCKET_NAME = _r2_bucket
    AWS_S3_ENDPOINT_URL     = _r2_endpoint
    AWS_DEFAULT_ACL         = 'private'
    AWS_S3_FILE_OVERWRITE   = False
    AWS_QUERYSTRING_AUTH    = True
    AWS_QUERYSTRING_EXPIRE  = 3600
    DEFAULT_FILE_STORAGE    = 'storages.backends.s3boto3.S3Boto3Storage'
    MEDIA_URL = f'{_r2_endpoint}/{_r2_bucket}/'
else:
    MEDIA_ROOT = BASE_DIR / 'media'
    MEDIA_URL  = '/media/'

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
    CELERY_BROKER_URL      = _redis_url
    CELERY_RESULT_BACKEND  = _redis_url
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
    },
}
