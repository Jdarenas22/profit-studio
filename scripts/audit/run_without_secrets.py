"""Ejecuta Django de desarrollo sin leer .env ni usar datos locales reales."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
for name in tuple(os.environ):
    if name.startswith(('GEMINI_', 'FDC_', 'WOMPI_', 'WHATSAPP_CLOUD_', 'R2_', 'AWS_', 'EMAIL_', 'REDIS_', 'DATABASE_', 'TRAINER_')):
        os.environ.pop(name, None)
os.environ.update(DJANGO_SETTINGS_MODULE='config.settings.development',
                  AI_PLANS_MODE='simulated', WHATSAPP_CLOUD_MODE='simulated',
                  SECRET_KEY='audit-only-not-for-production-8391792461',
                  PYTHONDONTWRITEBYTECODE='1')
import environ
environ.Env.read_env = classmethod(lambda cls, *args, **kwargs: None)
from django.conf import settings
settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
from django.core.management import execute_from_command_line
execute_from_command_line(['manage.py', *sys.argv[1:]])
