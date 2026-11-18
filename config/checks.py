"""Comprobaciones del sistema propias del proyecto (`python manage.py check --deploy`).

- profit.W001: la SECRET_KEY no cumple los criterios de security.W009 de Django.
- profit.W002: con R2 activo, los comprobantes de pago no tienen un bucket privado propio.

Son de despliegue (deploy=True): no aparecen en runserver, migrate ni en las pruebas, solo con
--deploy (y check_infra repite el diagnóstico). Los mensajes nombran variables, nunca valores.
Por defecto son avisos; con SECURITY_STRICT_SECRETS=true production.py ya no arranca (ver
config/security.py). Se registran en apps/public/apps.py.
"""
from django.conf import settings
from django.core.checks import Tags, Warning, register
from django.core.exceptions import ImproperlyConfigured

from config import security


@register(Tags.security, deploy=True)
def check_secret_key_strength(app_configs, **kwargs):
    try:
        secret = settings.SECRET_KEY
    except (ImproperlyConfigured, AttributeError):
        secret = ''
    reasons = security.secret_key_weaknesses(secret)
    if not reasons:
        return []
    return [Warning(
        'SECRET_KEY no cumple los criterios de seguridad de Django: ' + '; '.join(reasons) + '.',
        hint=(security.SECRET_KEY_ACTION_TEXT + ' Con SECURITY_STRICT_SECRETS=true producción no '
              'arranca con esta clave.'),
        id='profit.W001',
    )]


@register(Tags.security, deploy=True)
def check_receipts_bucket(app_configs, **kwargs):
    media_bucket = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '') or ''
    if not media_bucket:      # sin R2: los comprobantes van al disco y /media/receipts/ responde 404
        return []
    problem = security.receipts_bucket_problem(media_bucket, getattr(settings, 'RECEIPTS_BUCKET_NAME', ''))
    if not problem:
        return []
    return [Warning(
        security.RECEIPTS_PROBLEM_TEXT[problem] + ' ' + security.RECEIPTS_RISK_TEXT,
        hint=(security.RECEIPTS_ACTION_TEXT + ' Con SECURITY_STRICT_SECRETS=true producción no '
              'arranca así.'),
        id='profit.W002',
    )]
