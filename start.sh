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
    echo ">>> ERROR: DATABASE_URL no esta definida. Produccion requiere PostgreSQL; no se arrancara con SQLite." >&2
    exit 1
fi
echo ">>> PORT = $PORT"

# 2. Crear directorio de media (fotos de perfil, imágenes de ejercicios, comprobantes)
mkdir -p "$(pwd)/media/profiles" "$(pwd)/media/exercises/images" "$(pwd)/media/exercises/videos" 2>/dev/null || true

# 3a. Esperar a la base de datos. Con "App Sleeping" de Railway, el servicio web y
#     Postgres se detienen juntos; al despertar, el web arranca ANTES de que Postgres
#     acepte conexiones y el migrate fallaria con "Connection refused". Se reintenta
#     la conexion cada 3 s hasta DB_WAIT_SECONDS (opcional, por defecto 90).
#     Solo se imprime la clase del error, nunca el mensaje (podria traer host/usuario).
#     Si se agota el tiempo, se aborta: no se sirve con la base a medias.
export DB_WAIT_SECONDS="${DB_WAIT_SECONDS:-90}"
echo ">>> Esperando a la base de datos (maximo ${DB_WAIT_SECONDS}s)..."
if python - <<'PY'
import os
import sys
import time

INTERVAL = 3  # segundos entre intentos

raw = os.environ.get("DB_WAIT_SECONDS", "90")
try:
    wait = max(0, int(raw))
except ValueError:
    print(">>> ADVERTENCIA: DB_WAIT_SECONDS no es un numero entero; se usan 90 s.", flush=True)
    wait = 90
total = max(1, -(-wait // INTERVAL))  # techo de wait / INTERVAL, minimo 1 intento

try:
    import django
    django.setup()
    from django.db import connection
    vendor = connection.vendor
except Exception as exc:  # settings o driver mal configurados: que lo reporte el migrate con su salida completa
    print(f">>> ADVERTENCIA: no se pudo cargar Django para esperar la base ({type(exc).__name__}); se sigue al migrate.", flush=True)
    sys.exit(0)

if vendor == "sqlite":
    print(">>> Base SQLite: no hay nada que esperar.", flush=True)
    sys.exit(0)

# Que un intento no se quede colgado si la red no responde (parametro de libpq/Postgres)
if vendor == "postgresql":
    connection.settings_dict.setdefault("OPTIONS", {}).setdefault("connect_timeout", 5)

for n in range(1, total + 1):
    try:
        connection.ensure_connection()
        connection.close()
        print(f">>> Base de datos lista (intento {n}/{total}).", flush=True)
        sys.exit(0)
    except Exception as exc:
        print(f">>> Base de datos no disponible todavia ({type(exc).__name__}), intento {n}/{total}.", flush=True)
        try:
            connection.close()
        except Exception:
            pass
        if n < total:
            time.sleep(INTERVAL)

sys.exit(1)
PY
then
    :
else
    echo ">>> ERROR: la base de datos no respondio en ${DB_WAIT_SECONDS}s. Abortando el arranque." >&2
    exit 1
fi

# 3b. Migraciones — si fallan, NO se arranca la app (evita servir con la base a medias)
echo ">>> Ejecutando migrate..."
if python manage.py migrate --noinput 2>&1; then
    echo ">>> migrate OK"
else
    echo ">>> ERROR: migrate FALLO. Abortando el arranque." >&2
    exit 1
fi

# 4. Archivos estáticos — normalmente ya están: Railway ejecuta collectstatic en el BUILD
#    (railway.toml > buildCommand) y quedan dentro de la imagen. Aquí solo se repite si
#    falta la carpeta (p. ej. build sin variables o arranque local). Se tolera el fallo a
#    propósito: production.py usa CompressedStaticFilesStorage (sin manifest), así que la
#    app arranca y el login/health siguen funcionando aunque falte algún archivo estático
#    (solo se vería sin estilos). Se avisa en voz alta para corregirlo.
if [ -n "$(ls -A staticfiles 2>/dev/null)" ]; then
    echo ">>> staticfiles ya generado en el build; se omite collectstatic."
else
    echo ">>> staticfiles no existe; ejecutando collectstatic..."
    if python manage.py collectstatic --noinput 2>&1; then
        echo ">>> collectstatic OK"
    else
        echo ">>> ADVERTENCIA: collectstatic FALLO (se continua; la web puede verse sin estilos)" >&2
    fi
fi

# 5. Cuenta de la entrenadora principal (superusuaria 'yiseth').
#    Solo se crea si hay una contraseña inicial en el ENTORNO (Railway > Variables):
#    TRAINER_INITIAL_PASSWORD, o TRAINER_PASSWORD por compatibilidad.
#    create_trainer la lee del entorno: no se pasa por argumentos ni se imprime.
#    Si la cuenta ya existe, no se toca su contraseña.
if [ -n "${TRAINER_INITIAL_PASSWORD:-}" ] || [ -n "${TRAINER_PASSWORD:-}" ]; then
    echo ">>> Creando cuenta de entrenadora principal (si no existe)..."
    if python manage.py create_trainer         --username yiseth         --first-name Yiseth         --last-name "Misas García"         --email profitstudio075@gmail.com         --superuser         2>&1; then
        echo ">>> create_trainer OK"
    else
        echo ">>> ADVERTENCIA: create_trainer FALLO (se continua)" >&2
    fi
else
    echo ">>> AVISO: no hay TRAINER_INITIAL_PASSWORD ni TRAINER_PASSWORD; no se crea la cuenta 'yiseth'."
    echo ">>>        Si aun no existe, defínela en Railway o ejecuta: python manage.py create_trainer --username yiseth --superuser"
fi

# 6. Tareas posteriores al arranque, FUERA del camino crítico. Con "App Sleeping" la primera
#    visita tras dormir esperaba a load_foods (~20 s) y check_infra, y el navegador daba
#    timeout. Ahora corren en segundo plano mientras gunicorn ya atiende (/health/ no usa la
#    base). Ninguna aborta nada: solo dejan su resultado en los logs de Railway. Son
#    repetibles, no pisan datos validados y la web no depende de ellas para responder.

# 6a. Revisión de infraestructura (base, R2, Wompi, correo): ESTRICTAMENTE no bloqueante:
#     sin --smtp (no abre conexión al correo), con tope de tiempo (CHECK_INFRA_TIMEOUT, por
#     defecto 60 s) y, pase lo que pase (error, código distinto de 0 o cuelgue), no afecta al
#     servidor. check_infra no imprime secretos.
run_check_infra() {
    local timeout_s="${CHECK_INFRA_TIMEOUT:-60}"
    case "$timeout_s" in
        ''|*[!0-9]*) timeout_s=60 ;;
    esac
    echo ">>> [segundo plano] Revisión de infraestructura (check_infra, máximo ${timeout_s}s)..."
    local rc
    if command -v timeout >/dev/null 2>&1; then
        timeout -k 5 "$timeout_s" python manage.py check_infra </dev/null 2>&1
        rc=$?
    else
        # Sin 'timeout' en el contenedor: el comando corre en segundo plano y un vigilante lo
        # termina si pasa del tope.
        python manage.py check_infra </dev/null 2>&1 &
        local pid=$!
        ( sleep "$timeout_s"; kill -TERM "$pid" 2>/dev/null; sleep 5; kill -KILL "$pid" 2>/dev/null ) >/dev/null 2>&1 &
        local watchdog=$!
        wait "$pid" 2>/dev/null
        rc=$?
        kill "$watchdog" 2>/dev/null
        wait "$watchdog" 2>/dev/null
    fi
    if [ "$rc" -eq 0 ]; then
        echo ">>> [segundo plano] check_infra OK"
    else
        case "$rc" in
            124|137|143) echo ">>> [segundo plano] check_infra se detuvo por pasar el tope de ${timeout_s}s." ;;
        esac
        echo ">>> [segundo plano] check_infra terminó con avisos/errores (revisa arriba)."
    fi
}

# 6b. Datos iniciales: categorías de ejercicios y planes de membresía, y después el catálogo
#     de alimentos de los planes de alimentación (nunca pisa un alimento ya validado por un
#     profesional). El orden importa: primero load_initial_data y luego load_foods.
run_load_data() {
    echo ">>> [segundo plano] Cargando datos iniciales..."
    python manage.py load_initial_data </dev/null 2>&1 && echo ">>> [segundo plano] load_initial_data OK" || echo ">>> [segundo plano] load_initial_data FALLO (continuando)"
    echo ">>> [segundo plano] Cargando catálogo de alimentos..."
    python manage.py load_foods </dev/null 2>&1 && echo ">>> [segundo plano] load_foods OK" || echo ">>> [segundo plano] load_foods FALLO (continuando)"
}

echo ">>> Lanzando check_infra y la carga de datos en segundo plano (no bloquean el arranque)..."
run_check_infra &
run_load_data &

# 7. Gunicorn — siempre arranca (si llegamos aquí, la base está migrada). `exec` lo deja como
#    proceso principal; los trabajos en segundo plano de arriba siguen su curso.
echo ">>> Iniciando Gunicorn en 0.0.0.0:$PORT..."
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:$PORT \
    --workers 2 \
    --timeout 120 \
    --log-level info \
    --log-file -
