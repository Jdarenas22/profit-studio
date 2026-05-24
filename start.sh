#!/bin/bash
# Script de arranque para Railway
# Garantiza que siempre se usen los settings de producción

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.production}"

echo "==> Settings: $DJANGO_SETTINGS_MODULE"
echo "==> DATABASE_URL definido: ${DATABASE_URL:+SI}${DATABASE_URL:-NO}"

echo "==> Ejecutando migraciones..."
python manage.py migrate --noinput || echo "ADVERTENCIA: migrate falló (continuando...)"

echo "==> Recopilando archivos estáticos..."
python manage.py collectstatic --noinput --clear || echo "ADVERTENCIA: collectstatic falló (continuando...)"

echo "==> Iniciando Gunicorn en puerto $PORT..."
exec gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --log-file -
