#!/bin/bash
# Script de arranque Railway — ProFit Studio

# 1. Settings: usa production siempre (aunque DJANGO_SETTINGS_MODULE esté vacío)
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.production}"
PORT="${PORT:-8000}"
echo ">>> DJANGO_SETTINGS_MODULE = $DJANGO_SETTINGS_MODULE"
# Nunca imprimir el VALOR de DATABASE_URL (contiene usuario y contraseña de la base)
if [ -n "${DATABASE_URL:-}" ]; then
    echo ">>> DATABASE_URL definida: SI"
else
    echo ">>> DATABASE_URL definida: NO (usando SQLite)"
fi
echo ">>> PORT = $PORT"

# 2. Crear directorio de media (fotos de perfil, imágenes de ejercicios, comprobantes)
mkdir -p "$(pwd)/media/profiles" "$(pwd)/media/exercises/images" "$(pwd)/media/exercises/videos" 2>/dev/null || true

# 3. Migraciones — si fallan, NO se arranca la app (evita servir con la base a medias)
echo ">>> Ejecutando migrate..."
if python manage.py migrate --noinput 2>&1; then
    echo ">>> migrate OK"
else
    echo ">>> ERROR: migrate FALLO. Abortando el arranque." >&2
    exit 1
fi

# 4. Archivos estáticos — se tolera el fallo a propósito: production.py usa
#    CompressedStaticFilesStorage (sin manifest), así que la app arranca y el
#    login/health siguen funcionando aunque falte algún archivo estático
#    (solo se vería sin estilos). Se avisa en voz alta para corregirlo.
echo ">>> Ejecutando collectstatic..."
if python manage.py collectstatic --noinput 2>&1; then
    echo ">>> collectstatic OK"
else
    echo ">>> ADVERTENCIA: collectstatic FALLO (se continua; la web puede verse sin estilos)" >&2
fi

# 5. Cuenta de la entrenadora principal (superusuaria 'yiseth').
#    Solo se crea si hay una contraseña inicial en el ENTORNO (Railway > Variables):
#    TRAINER_INITIAL_PASSWORD, o TRAINER_PASSWORD por compatibilidad.
#    create_trainer la lee del entorno: no se pasa por argumentos ni se imprime.
#    Si la cuenta ya existe, no se toca su contraseña.
if [ -n "${TRAINER_INITIAL_PASSWORD:-}" ] || [ -n "${TRAINER_PASSWORD:-}" ]; then
    echo ">>> Creando cuenta de entrenadora principal (si no existe)..."
    if python manage.py create_trainer \
        --username yiseth \
        --first-name Yiseth \
        --last-name "Misas García" \
        --email profitstudio075@gmail.com \
        --superuser \
        2>&1; then
        echo ">>> create_trainer OK"
    else
        echo ">>> ADVERTENCIA: create_trainer FALLO (se continua)" >&2
    fi
else
    echo ">>> AVISO: no hay TRAINER_INITIAL_PASSWORD ni TRAINER_PASSWORD; no se crea la cuenta 'yiseth'."
    echo ">>>        Si aun no existe, defínela en Railway o ejecuta: python manage.py create_trainer --username yiseth --superuser"
fi

# 6. Cargar datos iniciales de ejercicios (continúa aunque falle)
echo ">>> Cargando datos iniciales..."
python manage.py load_initial_data 2>&1 && echo ">>> load_initial_data OK" || echo ">>> load_initial_data FALLO (continuando)"

# 7. Gunicorn — siempre arranca (si llegamos aquí, la base está migrada)
echo ">>> Iniciando Gunicorn en 0.0.0.0:$PORT..."
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:$PORT \
    --workers 2 \
    --timeout 120 \
    --log-level info \
    --log-file -
