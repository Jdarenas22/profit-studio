"""Modo de los avisos y requisitos para enviar (contrato, secciones 8 y 16, pregunta 14).

`WHATSAPP_CLOUD_MODE`:
- `off` (por defecto sin DEBUG): todo apagado; las rutas responden 404.
- `simulated` (por defecto con DEBUG): flujo completo con un cliente falso, sin red ni costo.
- `live`: WhatsApp Business Cloud API de Meta. Además de las variables obligatorias (las exige
  production.py), NO se ofrece activar ni se envía nada mientras falte un pendiente legal
  (`legal_missing`). En `off`/`simulated` los pendientes legales no bloquean.
"""
import re
from urllib.parse import urlsplit

from django.conf import settings

MODE_OFF = 'off'
MODE_SIMULATED = 'simulated'
MODE_LIVE = 'live'
MODES = (MODE_OFF, MODE_SIMULATED, MODE_LIVE)

LIVE_REQUIRED = (
    'WHATSAPP_CLOUD_ACCESS_TOKEN', 'WHATSAPP_CLOUD_PHONE_NUMBER_ID', 'WHATSAPP_CLOUD_WABA_ID',
    'WHATSAPP_CLOUD_APP_SECRET', 'WHATSAPP_CLOUD_VERIFY_TOKEN', 'WHATSAPP_CLOUD_GRAPH_VERSION',
)
MIN_VERIFY_TOKEN_LENGTH = 32
GRAPH_VERSION_RE = re.compile(r'^v\d+\.\d+$')


def mode():
    """Modo vigente. Vacío = `simulated` con DEBUG y `off` sin él. Desconocido = `off`."""
    value = (getattr(settings, 'WHATSAPP_CLOUD_MODE', '') or '').strip().lower()
    if not value:
        return MODE_SIMULATED if settings.DEBUG else MODE_OFF
    return value if value in MODES else MODE_OFF


def enabled():
    return mode() != MODE_OFF


def is_live():
    return mode() == MODE_LIVE


def setting(name, default=''):
    return getattr(settings, name, default)


def missing_live_settings():
    """Variables obligatorias de `live` que faltan (solo nombres, nunca valores)."""
    missing = [name for name in LIVE_REQUIRED if not str(setting(name) or '').strip()]
    token = str(setting('WHATSAPP_CLOUD_VERIFY_TOKEN') or '')
    if token and len(token) < MIN_VERIFY_TOKEN_LENGTH:
        missing.append(f'WHATSAPP_CLOUD_VERIFY_TOKEN (mínimo {MIN_VERIFY_TOKEN_LENGTH} caracteres)')
    version = str(setting('WHATSAPP_CLOUD_GRAPH_VERSION') or '')
    if version and not GRAPH_VERSION_RE.match(version):
        missing.append('WHATSAPP_CLOUD_GRAPH_VERSION (formato vNN.N)')
    return missing


def _https_url(value):
    parts = urlsplit(str(value or '').strip())
    return parts.scheme == 'https' and bool(parts.netloc)


def site_url():
    """Dominio definitivo sin barra final ('' si no está definido o no es https)."""
    value = str(setting('WHATSAPP_CLOUD_SITE_URL') or '').strip().rstrip('/')
    return value if _https_url(value) else ''


def legal_missing():
    """Pendientes legales (pregunta 14) que bloquean `live`. Lista de textos para la superusuaria."""
    from apps.health.models import PURPOSE_WHATSAPP
    from apps.health.services import current_text

    missing = []
    if not site_url():
        missing.append('WHATSAPP_CLOUD_SITE_URL (dominio definitivo, https)')
    if not _https_url(setting('PRIVACY_POLICY_URL')):
        missing.append('PRIVACY_POLICY_URL (política de tratamiento de datos, https)')
    if not setting('WHATSAPP_CLOUD_LEGAL_REVIEWED', False):
        missing.append('WHATSAPP_CLOUD_LEGAL_REVIEWED=true (texto revisado por un abogado)')
    text = current_text(PURPOSE_WHATSAPP)
    if text is None or text.has_placeholders:
        missing.append('texto de consentimiento de WhatsApp sin marcadores [...] (Admin > Textos de consentimiento)')
    return missing


def sending_allowed():
    """¿Se puede ofrecer activar y enviar avisos ahora? `off` nunca; `live` solo sin pendientes."""
    current = mode()
    if current == MODE_OFF:
        return False
    if current == MODE_LIVE:
        return not missing_live_settings() and not legal_missing()
    return True
