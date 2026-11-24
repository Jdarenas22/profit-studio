"""Reglas de seguridad de la configuración, compartidas por production.py, las comprobaciones
del sistema (config/checks.py) y `check_infra`.

Python puro: no importa los settings (production.py lo usa mientras se están cargando).
Ninguna función devuelve ni registra el VALOR de un secreto: solo motivos en texto.

Modo gradual (SECURITY_STRICT_SECRETS):
- Sin la variable, o con false (lo de hoy en Railway): una SECRET_KEY débil o un bucket de
  comprobantes no separado solo generan avisos (profit.W001 / profit.W002 y check_infra).
- Con SECURITY_STRICT_SECRETS=true: production.py lanza ImproperlyConfigured y el servidor
  no arranca hasta corregirlo. Activarla solo después de confirmar en Railway que la
  SECRET_KEY es fuerte y que R2_RECEIPTS_BUCKET_NAME es un bucket privado distinto.
"""

STRICT_ENV_NAME = 'SECURITY_STRICT_SECRETS'

# Mismos criterios que security.W009 de Django (django/core/checks/security/base.py).
# Se copian (y una prueba confirma que siguen iguales) para no importar django.core.checks
# mientras se cargan los settings.
SECRET_KEY_MIN_LENGTH = 50
SECRET_KEY_MIN_UNIQUE_CHARACTERS = 5
SECRET_KEY_INSECURE_PREFIX = 'django-insecure-'

# Valores de ejemplo que ya se rechazaban siempre (base.py y .env.example).
PLACEHOLDER_PREFIXES = ('cambia-esta', 'changeme', 'change-me', 'tu-clave')

_TRUE = ('true', '1', 'yes', 'on', 'si', 'sí')
_FALSE = ('', 'false', '0', 'no', 'off')


def parse_bool_flag(name, raw):
    """Booleano ESTRICTO de una variable de entorno. Vacío = False. Un valor desconocido (un error de
    tipeo como "ture") es un error (ValueError con el NOMBRE de la variable, nunca el valor): mejor
    avisarlo que dejar apagado lo que se quiso encender sin que nadie lo note."""
    value = str(raw if raw is not None else '').strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f'{name} debe ser true o false.')


def parse_strict_flag(raw):
    """Interpreta SECURITY_STRICT_SECRETS (ver `parse_bool_flag`)."""
    return parse_bool_flag(STRICT_ENV_NAME, raw)


def secret_key_is_placeholder(secret):
    """Vacía o valor de ejemplo/desarrollo. Esto detiene producción SIEMPRE (no es gradual)."""
    value = (secret or '').strip()
    lowered = value.lower()
    return not value or 'insecure' in lowered or lowered.startswith(PLACEHOLDER_PREFIXES)


def secret_key_weaknesses(secret):
    """Motivos por los que Django marcaría la clave con security.W009 (lista vacía = fuerte),
    más los valores de ejemplo del proyecto. Nunca incluye la clave ni su longitud exacta."""
    value = secret or ''
    reasons = []
    if secret_key_is_placeholder(value):
        reasons.append('es un valor de ejemplo o de desarrollo')
    if len(value) < SECRET_KEY_MIN_LENGTH:
        reasons.append(f'tiene menos de {SECRET_KEY_MIN_LENGTH} caracteres')
    if len(set(value)) < SECRET_KEY_MIN_UNIQUE_CHARACTERS:
        reasons.append(f'tiene menos de {SECRET_KEY_MIN_UNIQUE_CHARACTERS} caracteres distintos')
    if value.startswith(SECRET_KEY_INSECURE_PREFIX):
        reasons.append(f'empieza por "{SECRET_KEY_INSECURE_PREFIX}"')
    return reasons


def receipts_bucket_problem(media_bucket, receipts_bucket):
    """Con R2 activo: None si los comprobantes tienen su propio bucket; 'missing' si
    R2_RECEIPTS_BUCKET_NAME falta; 'same' si es el mismo bucket de la media pública."""
    receipts = (receipts_bucket or '').strip()
    if not receipts:
        return 'missing'
    if receipts == (media_bucket or '').strip():
        return 'same'
    return None


RECEIPTS_PROBLEM_TEXT = {
    'missing': 'R2_RECEIPTS_BUCKET_NAME no está definido: los comprobantes de pago se guardan en el '
               'bucket de media (R2_BUCKET_NAME).',
    'same': 'R2_RECEIPTS_BUCKET_NAME es igual a R2_BUCKET_NAME: los comprobantes de pago comparten '
            'el bucket de media.',
}
RECEIPTS_RISK_TEXT = ('Si ese bucket tiene acceso público (r2.dev o dominio personalizado), los '
                      'comprobantes serían públicos para quien tenga la URL.')
RECEIPTS_ACTION_TEXT = ('Crea en Cloudflare > R2 un bucket PRIVADO (sin r2.dev ni dominio propio) y '
                        'define R2_RECEIPTS_BUCKET_NAME con su nombre en Railway > Variables.')
SECRET_KEY_ACTION_TEXT = ('Genera una clave nueva (python -c "import secrets; '
                          'print(secrets.token_urlsafe(60))") y cámbiala en Railway > Variables > '
                          'SECRET_KEY. Cambiarla cierra las sesiones abiertas.')
