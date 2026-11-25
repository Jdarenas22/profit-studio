from .base import *

DEBUG = env.bool('DEBUG', default=True)
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1', '[::1]'])
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])

# Quitar apps opcionales no instaladas en entorno local
_OPTIONAL_APPS = ['storages', 'debug_toolbar']
INSTALLED_APPS = [app for app in INSTALLED_APPS if app not in _OPTIONAL_APPS]

# ─── Base de datos local (SQLite) ───────────────────────────────────────────────
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# ─── Archivos multimedia locales ────────────────────────────────────────────────
STORAGES = {
    **STORAGES,
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
}
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# ─── Email SMTP configurado ────────────────────────────────────────────────────────────
EMAIL_BACKEND = env('EMAIL_BACKEND', default='django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = env('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default=EMAIL_HOST_USER or 'noreply@localhost')

# ─── Django Debug Toolbar (si está instalado) ───────────────────────────────────
try:
    if not DEBUG or not env.bool('DEBUG_TOOLBAR_ENABLED', default=False):
        raise ImportError
    import debug_toolbar  # noqa: F401
    INSTALLED_APPS += ['debug_toolbar']
    MIDDLEWARE.insert(0, 'debug_toolbar.middleware.DebugToolbarMiddleware')
    INTERNAL_IPS = ['127.0.0.1']
except ImportError:
    pass

# ─── Salud de la clienta: encendida en desarrollo (se puede apagar con HEALTH_FEATURES_ENABLED=false)
HEALTH_FEATURES_ENABLED = env.bool('HEALTH_FEATURES_ENABLED', default=True)

# ─── Axes relajado en desarrollo ────────────────────────────────────────────────
AXES_ENABLED = not DEBUG

# El mismo servidor atiende localhost y su URL HTTPS de publicación.
PUBLIC_BASE_URL = env('PUBLIC_BASE_URL', default='').rstrip('/')
if PUBLIC_BASE_URL:
    from urllib.parse import urlsplit
    from django.core.exceptions import ImproperlyConfigured
    _public = urlsplit(PUBLIC_BASE_URL)
    if (_public.scheme != 'https' or not _public.hostname or _public.username or _public.password
            or _public.path or _public.query or _public.fragment or _public.port not in (None,443)):
        raise ImproperlyConfigured('PUBLIC_BASE_URL debe ser un origen HTTPS sin ruta ni credenciales.')
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO','https')
    CSRF_TRUSTED_ORIGINS = [*CSRF_TRUSTED_ORIGINS, PUBLIC_BASE_URL]
    MIDDLEWARE.insert(0, 'config.middleware.PublicOriginMiddleware')
