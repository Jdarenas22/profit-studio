#!/bin/bash
# Script de arranque para Railway
# Garantiza que siempre se usen los settings de producción

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.production}"

echo "==> Usando settings: $DJANGO_SETTINGS_MODULE"
echo "==> Ejecutando migraciones..."
python manage.py migrate --noinput

echo "==> Recopilando archivos estáticos..."
python manage.py collectstatic --noinput --clear

echo "==> Iniciando Gunicorn..."
exec gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --log-file -
