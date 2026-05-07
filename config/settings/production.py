from .base import *

DEBUG = False
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS')

# ─── Base de datos PostgreSQL ───────────────────────────────────────────────────
DATABASES = {
    'default': env.db('DATABASE_URL')
}

# ─── Seguridad ──────────────────────────────────────────────────────────────────
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

# ─── Cloudflare R2 — almacenamiento de archivos ─────────────────────────────────
AWS_ACCESS_KEY_ID = env('R2_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = env('R2_SECRET_ACCESS_KEY')
AWS_STORAGE_BUCKET_NAME = env('R2_BUCKET_NAME')
AWS_S3_ENDPOINT_URL = env('R2_ENDPOINT_URL')
AWS_DEFAULT_ACL = 'private'
AWS_S3_FILE_OVERWRITE = False
AWS_QUERYSTRING_AUTH = True
AWS_QUERYSTRING_EXPIRE = 3600  # URLs firmadas válidas 1 hora

DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'
MEDIA_URL = f"{env('R2_ENDPOINT_URL')}/{env('R2_BUCKET_NAME')}/"

# ─── Cache Redis ────────────────────────────────────────────────────────────────
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': env('REDIS_URL'),
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        }
    }
}
SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
SESSION_CACHE_ALIAS = 'default'

# ─── Celery con Redis ───────────────────────────────────────────────────────────
CELERY_BROKER_URL = env('REDIS_URL')
CELERY_RESULT_BACKEND = env('REDIS_URL')

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
