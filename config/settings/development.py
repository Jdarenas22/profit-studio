from .base import *

DEBUG = True
ALLOWED_HOSTS = ['localhost', '127.0.0.1', '0.0.0.0', '*']

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
DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# ─── Email a consola ────────────────────────────────────────────────────────────
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# ─── Django Debug Toolbar (si está instalado) ───────────────────────────────────
try:
    import debug_toolbar  # noqa: F401
    INSTALLED_APPS += ['debug_toolbar']
    MIDDLEWARE.insert(0, 'debug_toolbar.middleware.DebugToolbarMiddleware')
    INTERNAL_IPS = ['127.0.0.1']
except ImportError:
    pass

# ─── Axes relajado en desarrollo ────────────────────────────────────────────────
AXES_ENABLED = False
