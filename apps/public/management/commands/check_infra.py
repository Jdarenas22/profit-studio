"""Revisa que la infraestructura (Railway, Cloudflare R2, Wompi, correo, Redis) esté bien configurada.

Uso:
    python manage.py check_infra            # tabla OK / AVISO / ERROR
    python manage.py check_infra --smtp     # además abre la conexión al correo (sin enviar nada)
    python manage.py check_infra --json     # resultado en JSON (para scripts)

Es de SOLO LECTURA: no escribe en la base, no sube archivos a R2 y no envía correos. Nunca
imprime valores secretos: solo nombres de variables, estados y códigos de error. Como red de
seguridad, antes de imprimir se reemplaza cualquier valor secreto conocido por "***".

Termina con código distinto de cero si alguna comprobación da ERROR.
"""
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.db import Error as DatabaseError
from django.db import connection

from config import security

OK = 'OK'
WARN = 'AVISO'
ERROR = 'ERROR'

R2_REQUIRED = ('R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_BUCKET_NAME', 'R2_ENDPOINT_URL')
WOMPI_VARS = ('WOMPI_PUBLIC_KEY', 'WOMPI_PRIVATE_KEY', 'WOMPI_INTEGRITY_SECRET', 'WOMPI_EVENTS_SECRET')

# Variables cuyo valor jamás debe aparecer en una salida.
SECRET_ENV_NAMES = (
    'SECRET_KEY', 'DATABASE_URL', 'REDIS_URL', 'TRAINER_INITIAL_PASSWORD', 'TRAINER_PASSWORD',
    'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_ENDPOINT_URL', 'EMAIL_HOST_PASSWORD',
    *WOMPI_VARS, 'GEMINI_API_KEY', 'WHATSAPP_CLOUD_ACCESS_TOKEN', 'WHATSAPP_CLOUD_APP_SECRET',
    'WHATSAPP_CLOUD_VERIFY_TOKEN',
)
SECRET_SETTING_NAMES = ('SECRET_KEY', 'EMAIL_HOST_PASSWORD', *WOMPI_VARS, 'GEMINI_API_KEY',
                        'WHATSAPP_CLOUD_ACCESS_TOKEN', 'WHATSAPP_CLOUD_APP_SECRET', 'WHATSAPP_CLOUD_VERIFY_TOKEN')

NETWORK_TIMEOUT_SECONDS = 10


# ─── Modelo del resultado ─────────────────────────────────────────────────────────

@dataclass
class Check:
    area: str
    name: str
    status: str
    detail: str
    action: str = ''


# ─── Fábricas de conexión (se reemplazan en las pruebas) ──────────────────────────

def default_s3_client_factory(endpoint_url, access_key, secret_key):
    """Cliente S3 de boto3 hacia R2. boto3 se importa aquí: en local puede no estar instalado."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        's3',
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name='auto',
        config=Config(
            signature_version='s3v4',
            connect_timeout=NETWORK_TIMEOUT_SECONDS,
            read_timeout=NETWORK_TIMEOUT_SECONDS,
            retries={'max_attempts': 1},
        ),
    )


def default_redis_ping(url):
    """PING a Redis con tiempos cortos (solo lectura). redis se importa aquí: en local puede faltar."""
    import redis

    client = redis.Redis.from_url(url, socket_connect_timeout=NETWORK_TIMEOUT_SECONDS,
                                  socket_timeout=NETWORK_TIMEOUT_SECONDS)
    try:
        return client.ping()
    finally:
        client.close()


def default_mail_connection_factory():
    """Conexión SMTP de Django con los ajustes del proyecto. open() autentica; no envía nada."""
    from django.core.mail import get_connection

    return get_connection(fail_silently=False, timeout=NETWORK_TIMEOUT_SECONDS)


@dataclass
class Context:
    settings: Any
    env: Mapping[str, str]
    smtp: bool = False
    s3_client_factory: Callable = default_s3_client_factory
    mail_connection_factory: Callable = default_mail_connection_factory
    redis_ping: Callable = default_redis_ping
    db_ok: bool = field(default=True, init=False)

    @property
    def settings_module(self):
        # Con override_settings (pruebas) Django deja SETTINGS_MODULE en None: se usa el entorno.
        return (getattr(self.settings, 'SETTINGS_MODULE', '')
                or self.env.get('DJANGO_SETTINGS_MODULE', '') or '')

    @property
    def is_production(self):
        return self.settings_module.endswith('.production')

    def env_value(self, name):
        return (self.env.get(name) or '').strip()


def get_setting(settings_, name, default=''):
    """getattr que tolera el ImproperlyConfigured de Django (p. ej. SECRET_KEY vacía)."""
    try:
        return getattr(settings_, name, default)
    except ImproperlyConfigured:
        return default


# ─── Comprobaciones (una función por tema) ────────────────────────────────────────

def check_settings(ctx):
    debug = bool(ctx.settings.DEBUG)
    module = ctx.settings_module or '(sin definir)'
    detail = f'{module}, DEBUG={debug}'
    if ctx.is_production and debug:
        return [Check('Django', 'Configuración en uso', ERROR, detail,
                      'En producción DEBUG debe estar apagado: revisa que config.settings.production '
                      'se esté usando y que no se fuerce DEBUG.')]
    if ctx.is_production:
        return [Check('Django', 'Configuración en uso', OK, detail)]
    if debug:
        return [Check('Django', 'Configuración en uso', OK, f'{detail} (entorno de desarrollo)')]
    return [Check('Django', 'Configuración en uso', WARN, detail,
                  'No es la configuración de producción y DEBUG está apagado. En Railway define '
                  'DJANGO_SETTINGS_MODULE=config.settings.production.')]


def check_secret_key_and_hosts(ctx):
    if not ctx.is_production:
        return [
            Check('Django', 'SECRET_KEY', OK, 'Se exige solo en producción (se omite aquí).'),
            Check('Django', 'ALLOWED_HOSTS', OK, 'Se exige solo en producción (se omite aquí).'),
        ]
    results = []
    secret = get_setting(ctx.settings, 'SECRET_KEY') or ''
    strict = bool(getattr(ctx.settings, 'SECURITY_STRICT_SECRETS', False))
    if security.secret_key_is_placeholder(secret):
        results.append(Check(
            'Django', 'SECRET_KEY', ERROR,
            'Falta o es un valor de ejemplo/desarrollo.',
            'En Railway > Variables define SECRET_KEY con un valor largo y aleatorio '
            '(por ejemplo: python -c "import secrets; print(secrets.token_urlsafe(60))").'))
    else:
        # Mismos criterios que security.W009 de Django y que el chequeo profit.W001.
        weaknesses = security.secret_key_weaknesses(secret)
        if weaknesses:
            results.append(Check(
                'Django', 'SECRET_KEY', ERROR if strict else WARN,
                'No cumple los criterios de seguridad de Django (security.W009): ' + '; '.join(weaknesses) + '.',
                security.SECRET_KEY_ACTION_TEXT + ('' if strict else
                                                   ' Con SECURITY_STRICT_SECRETS=true el servidor no arrancaría.')))
        else:
            results.append(Check('Django', 'SECRET_KEY', OK,
                                 'Definida y cumple los criterios de Django (longitud y variedad).'))

    if strict:
        results.append(Check('Django', 'SECURITY_STRICT_SECRETS', OK,
                             'Activo: una SECRET_KEY débil o un bucket de comprobantes no separado '
                             'impiden el arranque.'))
    else:
        results.append(Check('Django', 'SECURITY_STRICT_SECRETS', OK,
                             'Inactivo: la clave débil y el bucket de comprobantes solo se avisan.',
                             'Actívalo (SECURITY_STRICT_SECRETS=true) cuando SECRET_KEY y '
                             'R2_RECEIPTS_BUCKET_NAME estén en OK en esta revisión.'))

    hosts = [h for h in (getattr(ctx.settings, 'ALLOWED_HOSTS', []) or []) if h]
    if not hosts or '*' in hosts:
        results.append(Check(
            'Django', 'ALLOWED_HOSTS', ERROR,
            'Vacío o con comodín "*".',
            'En Railway > Variables define ALLOWED_HOSTS con tus dominios separados por coma, '
            'sin "*" (ej.: profit-studio.up.railway.app,www.tudominio.com).'))
    else:
        results.append(Check('Django', 'ALLOWED_HOSTS', OK, f'{len(hosts)} host(s) permitido(s), sin comodín.'))
    return results


def pending_migrations(conn=None):
    """Migraciones que faltan por aplicar (solo lectura: no aplica ninguna)."""
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(conn or connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return [f'{migration.app_label}.{migration.name}' for migration, _backwards in plan]


def check_database(ctx):
    vendor = connection.vendor
    try:
        connection.ensure_connection()
    except DatabaseError as exc:
        ctx.db_ok = False
        return [Check('Base de datos', 'Conexión', ERROR,
                      f'No se pudo conectar ({type(exc).__name__}).',
                      'Revisa DATABASE_URL en Railway > Variables y que el servicio de PostgreSQL esté activo.')]
    results = []
    if ctx.is_production and vendor == 'sqlite':
        results.append(Check(
            'Base de datos', 'Motor', ERROR,
            'SQLite en producción: los datos se pierden en cada redeploy de Railway.',
            'Conecta el servicio de PostgreSQL de Railway y define DATABASE_URL.'))
    else:
        results.append(Check('Base de datos', 'Motor', OK, f'Conexión correcta ({vendor}).'))
    try:
        pending = pending_migrations()
    except DatabaseError as exc:
        results.append(Check('Base de datos', 'Migraciones', ERROR,
                             f'No se pudo leer el estado de las migraciones ({type(exc).__name__}).',
                             'Revisa la conexión y ejecuta: python manage.py migrate'))
        return results
    if pending:
        results.append(Check(
            'Base de datos', 'Migraciones', ERROR,
            f'{len(pending)} migración(es) pendiente(s), por ejemplo {pending[0]}.',
            'Ejecuta: python manage.py migrate (en Railway lo hace start.sh al desplegar).'))
    else:
        results.append(Check('Base de datos', 'Migraciones', OK, 'Todas las migraciones están aplicadas.'))
    return results


def _validate_public_url(url):
    """Mismas reglas que config/settings/production.py: https, con host y sin ruta."""
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return False
    return not (
        parts.scheme != 'https' or not parts.hostname or port == -1
        or parts.path not in ('', '/') or parts.query or parts.fragment
        or parts.username or parts.password
    )


def _bucket_error(exc, bucket_label):
    """Explica un fallo de head_bucket sin mostrar el mensaje original (puede traer datos de la cuenta)."""
    code = ''
    response = getattr(exc, 'response', None)
    if isinstance(response, dict):
        code = str(response.get('Error', {}).get('Code', ''))
    if code in ('404', 'NoSuchBucket', 'NotFound'):
        return (f'El bucket no existe en esa cuenta (código {code}).',
                f'Revisa el nombre de {bucket_label} y R2_ENDPOINT_URL en Railway > Variables, '
                'o crea el bucket en Cloudflare > R2.')
    if code in ('403', 'AccessDenied', 'InvalidAccessKeyId', 'SignatureDoesNotMatch'):
        return (f'Las credenciales no tienen acceso a este bucket (código {code}).',
                'En Cloudflare > R2 > Manage API Tokens crea un token con permiso de lectura y '
                'escritura sobre el bucket y actualiza R2_ACCESS_KEY_ID y R2_SECRET_ACCESS_KEY.')
    suffix = f', código {code}' if code else ''
    return (f'No se pudo contactar el bucket ({type(exc).__name__}{suffix}).',
            'Revisa R2_ENDPOINT_URL (https://<ID-DE-CUENTA>.r2.cloudflarestorage.com) y la conexión.')


def check_r2(ctx):
    area = 'Cloudflare R2'
    present = {name: bool(ctx.env_value(name)) for name in R2_REQUIRED}
    if not any(present.values()):
        if ctx.is_production:
            return [Check(area, 'Credenciales', WARN,
                          'Sin R2: los archivos (fotos, videos, comprobantes) se guardan en disco y '
                          'se pierden en cada redeploy.',
                          'Define las variables R2_* en Railway > Variables (ver .env.example).')]
        return [Check(area, 'Credenciales', OK, 'Sin R2: los archivos se guardan en la carpeta media/ local.')]

    missing = [name for name, ok in present.items() if not ok]
    if missing:
        return [Check(area, 'Credenciales', ERROR,
                      'Configuración incompleta, faltan: ' + ', '.join(missing) + '.',
                      'Define las variables que faltan (o quita todas si no usas R2). Ver .env.example.')]

    results = [Check(area, 'Credenciales', OK, 'Las 4 variables obligatorias están definidas.')]
    bucket = ctx.env_value('R2_BUCKET_NAME')
    receipts = ctx.env_value('R2_RECEIPTS_BUCKET_NAME')
    endpoint = ctx.env_value('R2_ENDPOINT_URL')
    public_url = ctx.env_value('R2_PUBLIC_URL')

    endpoint_parts = urlsplit(endpoint)
    if endpoint_parts.scheme != 'https' or not endpoint_parts.hostname:
        results.append(Check(area, 'R2_ENDPOINT_URL', ERROR, 'No es una URL https válida.',
                             'Debe ser https://<ID-DE-CUENTA>.r2.cloudflarestorage.com (Cloudflare > R2 > Overview).'))
    else:
        results.append(Check(area, 'R2_ENDPOINT_URL', OK, 'Formato https correcto.'))

    if not public_url:
        results.append(Check(area, 'R2_PUBLIC_URL', WARN,
                             'No está definida: las imágenes y videos no se verían en el navegador.',
                             'Activa el acceso público del bucket de media (r2.dev o dominio propio) y '
                             'define R2_PUBLIC_URL=https://pub-xxxx.r2.dev (sin ruta).'))
    elif not _validate_public_url(public_url):
        results.append(Check(area, 'R2_PUBLIC_URL', ERROR,
                             'Mal formada: debe ser solo el origen, con https y sin ruta ni nombre de bucket.',
                             'Corrígela a la forma https://pub-xxxx.r2.dev (sin nada después del dominio).'))
    else:
        results.append(Check(area, 'R2_PUBLIC_URL', OK, 'Formato correcto (https, sin ruta).'))

    # Misma regla que production.py y el chequeo profit.W002 (config/security.py).
    receipts_problem = security.receipts_bucket_problem(bucket, receipts)
    if receipts_problem == 'missing':
        results.append(Check(area, 'Bucket de comprobantes', ERROR,
                             'R2_RECEIPTS_BUCKET_NAME no está definido: los comprobantes irían al bucket de media.',
                             'Crea un bucket PRIVADO en Cloudflare > R2 (sin r2.dev ni dominio) y define '
                             'R2_RECEIPTS_BUCKET_NAME. Con SECURITY_STRICT_SECRETS=true el servidor no arranca así.'))
    elif receipts_problem == 'same':
        results.append(Check(area, 'Bucket de comprobantes', ERROR,
                             'Es el mismo bucket que el de media; si ese es público, los comprobantes quedan públicos.',
                             'Crea un bucket PRIVADO distinto y ponlo en R2_RECEIPTS_BUCKET_NAME. Con '
                             'SECURITY_STRICT_SECRETS=true el servidor no arranca así.'))
    else:
        results.append(Check(area, 'Bucket de comprobantes', OK, 'Es distinto del bucket de media.'))

    # Conexión real (solo lectura): head_bucket no sube ni borra nada.
    try:
        client = ctx.s3_client_factory(endpoint, ctx.env_value('R2_ACCESS_KEY_ID'),
                                       ctx.env_value('R2_SECRET_ACCESS_KEY'))
    except ImportError:
        results.append(Check(area, 'Conexión', ERROR, 'boto3 no está instalado en este entorno.',
                             'Instala las dependencias: pip install -r requirements/base.txt'))
        return results
    except Exception as exc:  # noqa: BLE001 — cualquier fallo se reporta sin mostrar el mensaje
        results.append(Check(area, 'Conexión', ERROR, f'No se pudo crear el cliente S3 ({type(exc).__name__}).',
                             'Revisa R2_ENDPOINT_URL y las credenciales.'))
        return results

    targets = [('Bucket de media', bucket, 'R2_BUCKET_NAME')]
    if receipts and receipts != bucket:
        targets.append(('Bucket de comprobantes (conexión)', receipts, 'R2_RECEIPTS_BUCKET_NAME'))
    for label, name, var in targets:
        try:
            client.head_bucket(Bucket=name)
        except Exception as exc:  # noqa: BLE001
            detail, action = _bucket_error(exc, var)
            results.append(Check(area, label, ERROR, f'"{name}": {detail}', action))
        else:
            note = ''
            if var == 'R2_RECEIPTS_BUCKET_NAME':
                note = (' Recuerda: debe tener el acceso público apagado en Cloudflare '
                        '(eso no se puede comprobar desde aquí).')
            results.append(Check(area, label, OK, f'"{name}" existe y responde.{note}'))
    return results


def check_wompi(ctx):
    missing = [name for name in WOMPI_VARS if not (getattr(ctx.settings, name, '') or '').strip()]
    if not missing:
        return [Check('Wompi', 'Claves de pago', OK, 'Las 4 variables están definidas.')]
    if len(missing) == len(WOMPI_VARS):
        return [Check('Wompi', 'Claves de pago', WARN,
                      'No hay ninguna clave: los pagos online no funcionan (los pagos manuales sí).',
                      'Crea la cuenta en comercios.wompi.co y define ' + ', '.join(WOMPI_VARS) + '.')]
    return [Check('Wompi', 'Claves de pago', ERROR,
                  'Configuración incompleta, faltan: ' + ', '.join(missing) + '. Los pagos online no funcionarán.',
                  'Define las variables que faltan en Railway > Variables (panel de Wompi > Desarrolladores).')]


def check_email(ctx):
    area = 'Correo'
    backend = getattr(ctx.settings, 'EMAIL_BACKEND', '')
    if 'smtp' not in backend:
        if ctx.is_production:
            return [Check(area, 'Envío de correos', WARN,
                          'Backend de consola: los correos (recuperar contraseña) NO se envían, solo salen en el log.',
                          'Define EMAIL_HOST_USER y EMAIL_HOST_PASSWORD (contraseña de aplicación de Gmail).')]
        return [Check(area, 'Envío de correos', OK, 'Backend de consola: los correos salen por la terminal (desarrollo).')]

    user = (getattr(ctx.settings, 'EMAIL_HOST_USER', '') or '').strip()
    password = (getattr(ctx.settings, 'EMAIL_HOST_PASSWORD', '') or '').strip()
    host = getattr(ctx.settings, 'EMAIL_HOST', '')
    port = getattr(ctx.settings, 'EMAIL_PORT', '')
    if not user or not password:
        return [Check(area, 'Envío de correos', ERROR, 'SMTP activo pero falta usuario o contraseña.',
                      'Define EMAIL_HOST_USER y EMAIL_HOST_PASSWORD (contraseña de aplicación de Gmail).')]
    if not ctx.smtp:
        return [Check(area, 'Envío de correos', OK,
                      f'SMTP en {host}:{port} con usuario y contraseña definidos (sin probar la conexión).',
                      'Para probar el inicio de sesión sin enviar nada: python manage.py check_infra --smtp')]
    try:
        connection_ = ctx.mail_connection_factory()
        try:
            connection_.open()
        finally:
            try:
                connection_.close()
            except Exception:  # noqa: BLE001 — cerrar es best-effort
                pass
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, 'smtp_code', None)
        suffix = f', código {code}' if code else ''
        if code == 535 or type(exc).__name__ == 'SMTPAuthenticationError':
            action = ('Gmail rechazó las credenciales: usa una contraseña de aplicación (Cuenta de Google > '
                      'Seguridad > Contraseñas de aplicaciones) y actualiza EMAIL_HOST_PASSWORD.')
        else:
            action = f'Revisa EMAIL_HOST ({host}), EMAIL_PORT ({port}) y la conexión a internet.'
        return [Check(area, 'Conexión SMTP', ERROR,
                      f'No se pudo abrir la sesión en {host}:{port} ({type(exc).__name__}{suffix}).', action)]
    return [Check(area, 'Conexión SMTP', OK,
                  f'Conexión y autenticación correctas en {host}:{port}. No se envió ningún correo.')]


REDIS_BACKEND_PREFIX = 'django_redis.'


def check_cache(ctx):
    """Caché compartida (Redis) o memoria de cada proceso.

    Sin REDIS_URL, production.py usa LocMemCache: cada proceso de gunicorn tiene su propia
    memoria, así que los límites de frecuencia de apps/accounts/ratelimit.py se cuentan por
    proceso (con N procesos el límite real puede ser hasta N veces mayor). Las sesiones quedan
    en la base de datos y django-axes (bloqueo por intentos de inicio de sesión) también usa la
    base: ninguno de los dos se afecta. El despliegue NO depende de Redis: solo se avisa.
    Con REDIS_URL, las sesiones viven en Redis: si no responde, nadie puede iniciar sesión.
    """
    area = 'Caché'
    caches = getattr(ctx.settings, 'CACHES', None) or {}
    backend = str((caches.get('default') or {}).get('BACKEND', ''))
    redis_url = ctx.env_value('REDIS_URL')
    if not backend.startswith(REDIS_BACKEND_PREFIX):
        if not ctx.is_production:
            return [Check(area, 'Redis', OK, 'Caché en memoria local (desarrollo).')]
        return [Check(area, 'Redis', WARN,
                      'Sin REDIS_URL: la caché vive en la memoria de cada proceso de gunicorn. Los límites de '
                      'frecuencia del registro de cuentas se cuentan por proceso y pueden permitir hasta '
                      'N veces más intentos con N procesos. Las sesiones y django-axes usan la base de datos y '
                      'no se afectan. La web funciona igual.',
                      'Para límites compartidos: Railway > + New > Database > Redis y, en el servicio web, '
                      'Variables > Add Reference > REDIS_URL. Después vuelve a correr esta revisión.')]
    if not redis_url:
        return [Check(area, 'Redis', OK, 'Caché Redis configurada (sin REDIS_URL en el entorno: no se prueba).')]
    try:
        answered = ctx.redis_ping(redis_url)
    except ImportError:
        return [Check(area, 'Redis', ERROR, 'La biblioteca redis no está instalada en este entorno.',
                      'Instala las dependencias: pip install -r requirements/base.txt')]
    except Exception as exc:  # noqa: BLE001 — nunca se muestra el mensaje (puede traer host o clave)
        return [Check(area, 'Redis', ERROR,
                      f'Redis no responde ({type(exc).__name__}): las sesiones y los límites viven ahí, '
                      'así que nadie podría iniciar sesión.',
                      'Revisa que el servicio Redis de Railway esté activo y que REDIS_URL sea la referencia '
                      'correcta; si no lo vas a usar, borra REDIS_URL del servicio web.')]
    if not answered:
        return [Check(area, 'Redis', ERROR, 'Redis no respondió al PING.',
                      'Revisa el servicio Redis de Railway y REDIS_URL.')]
    return [Check(area, 'Redis', OK,
                  'Redis responde: límites de frecuencia y sesiones compartidos entre procesos.')]


def check_superuser(ctx):
    area = 'Cuentas'
    if not ctx.db_ok:
        return [Check(area, 'Superusuaria', WARN, 'Se omite: no hay conexión a la base de datos.')]
    from django.contrib.auth import get_user_model

    try:
        exists = get_user_model().objects.filter(is_superuser=True, is_active=True).exists()
    except DatabaseError as exc:
        return [Check(area, 'Superusuaria', WARN,
                      f'No se pudo consultar ({type(exc).__name__}); quizá faltan migraciones.',
                      'Aplica las migraciones y vuelve a ejecutar esta revisión.')]
    if exists:
        return [Check(area, 'Superusuaria', OK, 'Existe al menos una superusuaria activa.')]
    status = ERROR if ctx.is_production else WARN
    return [Check(area, 'Superusuaria', status,
                  'No hay ninguna superusuaria activa: nadie puede ver Pagos ni Entrenadores.',
                  'Define TRAINER_INITIAL_PASSWORD en Railway y vuelve a desplegar, o ejecuta: '
                  'python manage.py create_trainer --username yiseth --superuser')]


def purposes_pending_legal_review():
    from apps.health import services

    return services.purposes_with_placeholders()


def check_health_switches(ctx):
    area = 'Salud'
    enabled = bool(getattr(ctx.settings, 'HEALTH_FEATURES_ENABLED', False))
    privacy_url = (getattr(ctx.settings, 'PRIVACY_POLICY_URL', '') or '').strip()
    results = []

    if not enabled:
        results.append(Check(area, 'HEALTH_FEATURES_ENABLED', OK,
                             'Apagado: las pantallas de salud de la clienta responden 404 (lo esperado '
                             'hasta que un abogado apruebe los textos).'))
    else:
        pending = []
        if ctx.is_production and ctx.db_ok:
            try:
                pending = purposes_pending_legal_review()
            except DatabaseError:
                pending = []
        if pending:
            results.append(Check(area, 'HEALTH_FEATURES_ENABLED', WARN,
                                 'Encendido, pero los textos de consentimiento aún tienen marcadores [...] '
                                 f'o faltan ({", ".join(pending)}).',
                                 'Publica la versión definitiva en Admin > Textos de consentimiento (revisada por '
                                 'un abogado) o apaga HEALTH_FEATURES_ENABLED.'))
        else:
            results.append(Check(area, 'HEALTH_FEATURES_ENABLED', OK, 'Encendido.'))

    if not privacy_url:
        status = WARN if enabled else OK
        results.append(Check(area, 'PRIVACY_POLICY_URL', status,
                             'No está definida: no se muestra el enlace a la política de datos.',
                             'Antes de encender las pantallas de salud, define PRIVACY_POLICY_URL con la '
                             'dirección https de la política.' if enabled else ''))
    elif urlsplit(privacy_url).scheme != 'https':
        results.append(Check(area, 'PRIVACY_POLICY_URL', WARN, 'No es una dirección https.',
                             'Usa una dirección que empiece por https://'))
    else:
        results.append(Check(area, 'PRIVACY_POLICY_URL', OK, 'Definida (https).'))
    return results


def untagged_exercise_count():
    from apps.exercises.models import Exercise
    return Exercise.objects.filter(is_active=True, intensity='').count()


def nutrition_catalog_state():
    """(alimentos activos, activos sin validar, personas con credencial de nutrición)."""
    from apps.plans.models import Food, TrainerCredential
    active = Food.objects.filter(is_active=True)
    return (active.count(), active.filter(validated_by='').count(),
            TrainerCredential.objects.filter(can_validate_nutrition=True, user__is_active=True).count())


def check_ai_plans(ctx):
    area = 'IA'
    raw = (getattr(ctx.settings, 'AI_PLANS_MODE', '') or '').strip().lower()
    mode = raw or ('simulated' if getattr(ctx.settings, 'DEBUG', False) else 'off')
    results = []
    if mode not in ('off', 'simulated', 'live'):
        return [Check(area, 'AI_PLANS_MODE', ERROR, f'Valor no válido: {raw}.', 'Usa off, simulated o live.')]
    if mode == 'off':
        return [Check(area, 'AI_PLANS_MODE', OK, 'Apagado: los planes con IA no se ofrecen.')]
    if mode == 'simulated':
        status = WARN if ctx.is_production else OK
        results.append(Check(area, 'AI_PLANS_MODE', status,
                             'Modo de prueba: los borradores salen de un generador de ejemplo (sin Gemini) '
                             'y no se pueden aprobar en producción.',
                             'Para usar Gemini: GEMINI_API_KEY, GEMINI_MODEL, GEMINI_BILLING_CONFIRMED=true y '
                             'AI_PLANS_MODE=live.' if ctx.is_production else ''))
    else:
        missing = [name for name in ('GEMINI_API_KEY', 'GEMINI_MODEL')
                   if not str(get_setting(ctx.settings, name) or '').strip()]
        if not getattr(ctx.settings, 'GEMINI_BILLING_CONFIRMED', False):
            missing.append('GEMINI_BILLING_CONFIRMED=true')
        if missing:
            results.append(Check(area, 'AI_PLANS_MODE', ERROR, 'live sin configuración completa: falta ' + ', '.join(missing) + '.',
                                 'La clave debe ser de un proyecto de Google AI Studio con facturación habilitada.'))
        else:
            results.append(Check(area, 'AI_PLANS_MODE', OK,
                                 f'live con el modelo {get_setting(ctx.settings, "GEMINI_MODEL")} (conexión sin probar).',
                                 'Pruébala con: python manage.py check_gemini'))
    if not getattr(ctx.settings, 'HEALTH_FEATURES_ENABLED', False):
        results.append(Check(area, 'Planes con IA visibles', WARN,
                             'HEALTH_FEATURES_ENABLED está apagado: los planes con IA no se muestran.',
                             'Se encienden junto con las pantallas de salud.'))
    if ctx.db_ok:
        try:
            untagged = untagged_exercise_count()
        except DatabaseError:
            untagged = 0
        if untagged:
            results.append(Check(area, 'Etiquetas de ejercicios', WARN,
                                 f'{untagged} ejercicio(s) activo(s) sin etiquetar: no se proponen a clientas con '
                                 'lesiones o advertencias, ni a quien entrena en casa.',
                                 'python manage.py list_untagged_exercises y etiquétalos en el panel.'))
        professional_required = bool(getattr(ctx.settings, 'AI_PLANS_REQUIRE_PROFESSIONAL', False))
        try:
            foods, unvalidated, validators = nutrition_catalog_state()
        except DatabaseError:
            foods = unvalidated = validators = None
        if foods is not None:
            if not foods:
                results.append(Check(area, 'Alimentos', WARN,
                                     'El catálogo de alimentos está vacío: no se pueden armar planes de alimentación.',
                                     'python manage.py load_foods'))
            elif unvalidated and ctx.is_production and professional_required:
                results.append(Check(area, 'Alimentos', WARN,
                                     f'{unvalidated} de {foods} alimentos activos están sin validar: fuera de modo de '
                                     'desarrollo no se usan hasta que un nutricionista los valide.',
                                     'Admin > Alimentos > acción "Marcar como validados" (tras revisar las cifras).'))
            else:
                results.append(Check(area, 'Alimentos', OK, f'{foods} alimentos activos ({unvalidated} sin validar).'))
            if not professional_required:
                results.append(Check(area, 'Validación profesional', WARN,
                                     'SUSPENDIDA: cualquier entrenador aprueba planes de alimentación y se usan '
                                     'alimentos sin validar por un nutricionista.',
                                     'Para reactivarla define AI_PLANS_REQUIRE_PROFESSIONAL=true.'))
            elif not validators:
                results.append(Check(area, 'Quién aprueba la alimentación', WARN,
                                     'Nadie tiene la credencial de nutrición: ningún plan de alimentación se puede aprobar.',
                                     'Admin > Credenciales de validación (solo para quien pueda validarlos).'))
    return results


def whatsapp_state(settings_):
    """(modo, variables que faltan, pendientes legales, canal pausado, plantillas pausadas, fallidos 24 h).
    Misma regla que apps.whatsapp.conf, leída del objeto de settings recibido (las pruebas usan uno falso)."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.health.models import PURPOSE_WHATSAPP, ConsentTextVersion
    from apps.whatsapp.conf import GRAPH_VERSION_RE, LIVE_REQUIRED, MIN_VERIFY_TOKEN_LENGTH
    from apps.whatsapp.models import WhatsAppChannelState, WhatsAppMessage

    raw = (getattr(settings_, 'WHATSAPP_CLOUD_MODE', '') or '').strip().lower()
    mode = raw or ('simulated' if getattr(settings_, 'DEBUG', False) else 'off')
    missing, legal = [], []
    if mode == 'live':
        missing = [name for name in LIVE_REQUIRED if not str(get_setting(settings_, name) or '').strip()]
        token = str(get_setting(settings_, 'WHATSAPP_CLOUD_VERIFY_TOKEN') or '')
        if token and len(token) < MIN_VERIFY_TOKEN_LENGTH:
            missing.append('WHATSAPP_CLOUD_VERIFY_TOKEN (muy corta)')
        version = str(get_setting(settings_, 'WHATSAPP_CLOUD_GRAPH_VERSION') or '')
        if version and not GRAPH_VERSION_RE.match(version):
            missing.append('WHATSAPP_CLOUD_GRAPH_VERSION (formato vNN.N)')
    if mode != 'off':
        for name, label in (('WHATSAPP_CLOUD_SITE_URL', 'WHATSAPP_CLOUD_SITE_URL (https)'),
                            ('PRIVACY_POLICY_URL', 'PRIVACY_POLICY_URL (https)')):
            if urlsplit(str(getattr(settings_, name, '') or '').strip()).scheme != 'https':
                legal.append(label)
        if not getattr(settings_, 'WHATSAPP_CLOUD_LEGAL_REVIEWED', False):
            legal.append('WHATSAPP_CLOUD_LEGAL_REVIEWED=true')
        text = ConsentTextVersion.objects.filter(purpose=PURPOSE_WHATSAPP, is_current=True).first()
        if text is None or text.has_placeholders:
            legal.append('texto de consentimiento de WhatsApp sin marcadores')
    state = WhatsAppChannelState.objects.filter(pk=1).first()
    failed = WhatsAppMessage.objects.filter(status=WhatsAppMessage.STATUS_FAILED,
                                            created_at__gte=timezone.now() - timedelta(hours=24)).count()
    return mode, missing, legal, bool(state and state.paused), list(state.paused_templates) if state else [], failed


def check_whatsapp(ctx):
    area = 'WhatsApp'
    raw = (getattr(ctx.settings, 'WHATSAPP_CLOUD_MODE', '') or '').strip().lower()
    if raw not in ('', 'off', 'simulated', 'live'):
        return [Check(area, 'WHATSAPP_CLOUD_MODE', ERROR, f'Valor no válido: {raw}.', 'Usa off, simulated o live.')]
    if (raw or ('simulated' if getattr(ctx.settings, 'DEBUG', False) else 'off')) == 'off':
        return [Check(area, 'WHATSAPP_CLOUD_MODE', OK, 'Apagado: no se ofrecen ni se envían avisos.')]
    if not ctx.db_ok:
        return [Check(area, 'WHATSAPP_CLOUD_MODE', WARN, 'Sin base de datos: no se pudo revisar.')]
    try:
        mode, missing, legal, paused, paused_templates, failed = whatsapp_state(ctx.settings)
    except DatabaseError:
        return [Check(area, 'WHATSAPP_CLOUD_MODE', WARN, 'No se pudo leer el estado (¿faltan migraciones?).')]
    if mode == 'off':
        return [Check(area, 'WHATSAPP_CLOUD_MODE', OK, 'Apagado: no se ofrecen ni se envían avisos.')]
    results = []
    if mode == 'simulated':
        results.append(Check(area, 'WHATSAPP_CLOUD_MODE', WARN if ctx.is_production else OK,
                             'Modo de prueba: los avisos no salen a WhatsApp.'))
    elif missing:
        results.append(Check(area, 'WHATSAPP_CLOUD_MODE', ERROR, 'live sin configuración completa: falta '
                             + ', '.join(missing) + '.', 'Revisa la sección 8 de docs/contracts/whatsapp.md.'))
    else:
        results.append(Check(area, 'WHATSAPP_CLOUD_MODE', OK, 'live (conexión sin probar).',
                             'Pruébala con: python manage.py check_whatsapp'))
    if legal:
        results.append(Check(area, 'Pendientes legales', WARN if mode == 'live' else OK,
                             ('No se envía nada mientras falte: ' if mode == 'live' else 'Pendiente: ') + '; '.join(legal) + '.',
                             'Pregunta 14 de docs/contracts/whatsapp.md.' if mode == 'live' else ''))
    if paused:
        results.append(Check(area, 'Canal', ERROR, 'Pausado por un error de cuenta o token.',
                             'python manage.py check_whatsapp --resume'))
    if paused_templates:
        results.append(Check(area, 'Plantillas', WARN, 'Pausadas: ' + ', '.join(paused_templates) + '.',
                             'Revísalas en el Administrador de WhatsApp y luego: python manage.py check_whatsapp --resume'))
    if failed:
        results.append(Check(area, 'Fallidos (24 h)', WARN, f'{failed} aviso(s) no se pudieron enviar.',
                             'python manage.py check_whatsapp'))
    return results


CHECKS = (
    check_settings,
    check_secret_key_and_hosts,
    check_database,
    check_r2,
    check_wompi,
    check_email,
    check_cache,
    check_superuser,
    check_health_switches,
    check_ai_plans,
    check_whatsapp,
)


# ─── Ejecución y salida ───────────────────────────────────────────────────────────

def run_checks(ctx):
    results = []
    for check in CHECKS:
        try:
            results.extend(check(ctx))
        except Exception as exc:  # noqa: BLE001 — una comprobación rota no debe tumbar a las demás
            results.append(Check('Interno', check.__name__, ERROR,
                                 f'La comprobación falló ({type(exc).__name__}).',
                                 'Reporta este error a quien mantiene el sistema.'))
    return results


def collect_secrets(ctx):
    values = set()
    for name in SECRET_ENV_NAMES:
        values.add(ctx.env_value(name))
    for name in SECRET_SETTING_NAMES:
        values.add(str(get_setting(ctx.settings, name) or '').strip())
    try:
        values.add(str(ctx.settings.DATABASES['default'].get('PASSWORD') or '').strip())
    except Exception:  # noqa: BLE001
        pass
    # Valores muy cortos (p. ej. "1") borrarían texto normal y no son secretos útiles de proteger.
    return sorted((v for v in values if len(v) >= 6), key=len, reverse=True)


def redact(text, secrets):
    for secret in secrets:
        text = text.replace(secret, '***')
    return text


def redact_checks(results, secrets):
    return [
        Check(c.area, c.name, c.status, redact(c.detail, secrets), redact(c.action, secrets))
        for c in results
    ]


def summarize(results):
    return {
        'ok': sum(c.status == OK for c in results),
        'aviso': sum(c.status == WARN for c in results),
        'error': sum(c.status == ERROR for c in results),
    }


def render_json(ctx, results):
    return json.dumps({
        'settings_module': ctx.settings_module,
        'production': ctx.is_production,
        'summary': summarize(results),
        'checks': [asdict(c) for c in results],
    }, ensure_ascii=False, indent=2)


class Command(BaseCommand):
    help = ('Revisa la configuración de infraestructura (base de datos, Cloudflare R2, Wompi, correo) '
            'sin modificar nada y sin mostrar secretos. Termina con error si algo está mal.')

    def add_arguments(self, parser):
        parser.add_argument('--smtp', action='store_true', default=False,
                            help='Abre la conexión al servidor de correo y autentica, SIN enviar ningún mensaje.')
        parser.add_argument('--json', action='store_true', default=False, dest='as_json',
                            help='Imprime el resultado como JSON.')

    def build_context(self, options):
        return Context(
            settings=django_settings,
            env=os.environ,
            smtp=options['smtp'],
            s3_client_factory=default_s3_client_factory,
            mail_connection_factory=default_mail_connection_factory,
            redis_ping=default_redis_ping,
        )

    def handle(self, *args, **options):
        ctx = self.build_context(options)
        results = redact_checks(run_checks(ctx), collect_secrets(ctx))
        summary = summarize(results)

        if options['as_json']:
            self.stdout.write(render_json(ctx, results))
        else:
            self.write_table(ctx, results, summary)

        if summary['error']:
            raise CommandError(f'Hay {summary["error"]} comprobación(es) con ERROR.', returncode=1)

    def write_table(self, ctx, results, summary):
        styles = {OK: self.style.SUCCESS, WARN: self.style.WARNING, ERROR: self.style.ERROR}
        self.stdout.write('Revisión de infraestructura — ProFit Studio')
        self.stdout.write(f'Configuración: {ctx.settings_module or "(sin definir)"}')
        self.stdout.write('')
        name_width = max(len(c.name) for c in results)
        area_width = max(len(c.area) for c in results)
        for c in results:
            label = styles[c.status](f'{c.status:<5}')
            self.stdout.write(f'[{label}] {c.area:<{area_width}}  {c.name:<{name_width}}  {c.detail}')
            if c.action and c.status != OK:
                self.stdout.write(f'         Qué hacer: {c.action}')
            elif c.action:
                self.stdout.write(f'         Nota: {c.action}')
        self.stdout.write('')
        self.stdout.write(f'Resumen: {summary["ok"]} OK, {summary["aviso"]} AVISO, {summary["error"]} ERROR')
        if summary['error']:
            self.stdout.write(self.style.ERROR('Hay errores: corrígelos antes de desplegar.'))
