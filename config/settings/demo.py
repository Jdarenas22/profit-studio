"""Demostración académica local: base y servicios separados de desarrollo normal."""
from .development import *

DEMO_ROOT = BASE_DIR / '.demo'
DEMO_ROOT.mkdir(exist_ok=True)
ACADEMIC_DEMO = True
DEBUG = True
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', '[::1]']
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': DEMO_ROOT / 'demo.sqlite3'}}
MEDIA_ROOT = DEMO_ROOT / 'media'
STATIC_ROOT = DEMO_ROOT / 'staticfiles'
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'academic-demo'}}
SESSION_COOKIE_NAME = 'profit_academic_session'
CSRF_COOKIE_NAME = 'profit_academic_csrf'
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
WHATSAPP_CLOUD_MODE = 'off'
WOMPI_PUBLIC_KEY = WOMPI_PRIVATE_KEY = WOMPI_INTEGRITY_SECRET = WOMPI_EVENTS_SECRET = ''
HEALTH_FEATURES_ENABLED = True
AI_PLANS_REQUIRE_PROFESSIONAL = False
AI_PLANS_RUN_INLINE = True
AI_PLANS_MODE = env('DEMO_AI_MODE', default='simulated')
DEMO_FREE_TIER_CONFIRMED = env.bool('DEMO_FREE_TIER_CONFIRMED', default=False)
GEMINI_MODEL = env('GEMINI_MODEL', default='') or 'gemini-3.5-flash-lite'
GEMINI_BILLING_CONFIRMED = False
ROOT_URLCONF = 'config.urls_demo'
MIDDLEWARE = ['apps.plans.academic.DemoBoundaryMiddleware', *MIDDLEWARE]

# Presentación sin herramientas de depuración ni sus paneles de configuración.
MIDDLEWARE = [item for item in MIDDLEWARE if not item.startswith('debug_toolbar.')]
