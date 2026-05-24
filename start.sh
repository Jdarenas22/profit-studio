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

# 4. Crear cuenta de entrenadora Yiseth (solo si no existe)
echo ">>> Creando cuenta de entrenadora..."
python manage.py create_trainer \
    --username yiseth \
    --first-name Yiseth \
    --last-name "Misas García" \
    --email profitstudio075@gmail.com \
    --password "ProFit2024!" \
    2>&1 && echo ">>> create_trainer OK" || echo ">>> create_trainer FALLÓ (continuando)"

# 5. Cargar datos iniciales de ejercicios (continúa aunque falle)
echo ">>> Cargando datos iniciales..."
python manage.py load_initial_data 2>&1 && echo ">>> load_initial_data OK" || echo ">>> load_initial_data FALLÓ (continuando)"

# 6. Gunicorn — siempre arranca
echo ">>> Iniciando Gunicorn en 0.0.0.0:$PORT..."
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:$PORT \
    --workers 2 \
    --timeout 120 \
    --log-level info \
    --log-file -
