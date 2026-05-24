#!/bin/bash
# Script de arranque Railway — ProFit Studio

# 1. Settings: usa production siempre (aunque DJANGO_SETTINGS_MODULE esté vacío)
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.production}"
echo ">>> DJANGO_SETTINGS_MODULE = $DJANGO_SETTINGS_MODULE"
echo ">>> DATABASE_URL definida: ${DATABASE_URL:+SI}${DATABASE_URL:-NO (usando SQLite)}"
echo ">>> PORT = $PORT"

# 2. Migraciones (continúa aunque falle)
echo ">>> Ejecutando migrate..."
python manage.py migrate --noinput 2>&1 && echo ">>> migrate OK" || echo ">>> migrate FALLÓ (continuando)"

# 3. Archivos estáticos (continúa aunque falle)
echo ">>> Ejecutando collectstatic..."
python manage.py collectstatic --noinput 2>&1 && echo ">>> collectstatic OK" || echo ">>> collectstatic FALLÓ (continuando)"

# 4. Gunicorn — siempre arranca
echo ">>> Iniciando Gunicorn en 0.0.0.0:$PORT..."
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:$PORT \
    --workers 2 \
    --timeout 120 \
    --log-level info \
    --log-file -
