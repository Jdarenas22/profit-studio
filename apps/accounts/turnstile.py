"""Cloudflare Turnstile (antirrobots) para el registro y el reenvío del correo de confirmación.

Es OPCIONAL y viene apagado: solo se exige y se muestra cuando existen las dos variables de entorno
`TURNSTILE_SITE_KEY` (pública, va en la página) y `TURNSTILE_SECRET_KEY` (secreta, solo en el servidor).
Si falta cualquiera de las dos, `enabled()` es falso y nada cambia.

Verificación en el servidor (Cloudflare, consultado el 2026-11-24:
https://developers.cloudflare.com/turnstile/get-started/server-side-validation/):
- POST a https://challenges.cloudflare.com/turnstile/v0/siteverify con `secret`, `response` (el token del
  widget, máximo 2048 caracteres, vale 300 s y es de un solo uso) y `remoteip` (opcional).
- La respuesta es JSON con `success` (verdadero/falso) y `error-codes`.

Seguridad:
- Falla CERRADO: si Cloudflare no responde a tiempo, devuelve un error o algo que no es JSON, la persona
  recibe el mensaje genérico `MESSAGE` y se le pide reintentar. Nunca se deja pasar sin verificar cuando
  la función está encendida.
- Solo biblioteca estándar (`urllib`), tiempo de espera corto, sin seguir redirecciones (no reenviar la
  clave secreta a otra URL) y lectura limitada de la respuesta.
- Ni el token ni la clave secreta se escriben en logs ni en mensajes; los logs llevan solo el tipo de fallo.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

SITEVERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'
FORM_FIELD = 'cf-turnstile-response'          # campo oculto que agrega el widget dentro del <form>
TIMEOUT_SECONDS = 5
MAX_TOKEN_LENGTH = 2048
MAX_RESPONSE_BYTES = 64 * 1024
MESSAGE = 'No pudimos comprobar que eres una persona. Recarga la página e inténtalo de nuevo.'


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Nunca reenviar la clave secreta a una URL de redirección."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def site_key():
    return (getattr(settings, 'TURNSTILE_SITE_KEY', '') or '').strip()


def _secret_key():
    return (getattr(settings, 'TURNSTILE_SECRET_KEY', '') or '').strip()


def enabled():
    """Encendido solo con las DOS claves. Con una sola (configuración a medias) queda apagado."""
    return bool(site_key() and _secret_key())


def half_configured():
    """Hay una clave sin la otra: error de configuración que `check_infra` avisa."""
    return bool(site_key()) != bool(_secret_key())


def widget_context():
    """Para las plantillas: la clave pública si el widget debe pintarse; vacío si no."""
    return {'turnstile_site_key': site_key() if enabled() else ''}


def verify(token, remote_ip=None, opener=None):
    """True si la persona pasó la comprobación (o si la función está apagada).

    `opener` solo lo usan las pruebas (una función `abrir(request, timeout=...)`); en producción se usa
    `urllib` sin redirecciones.
    """
    if not enabled():
        return True
    token = (token or '').strip() if isinstance(token, str) else ''
    if not token or len(token) > MAX_TOKEN_LENGTH:
        return False
    fields = {'secret': _secret_key(), 'response': token}
    if remote_ip:
        fields['remoteip'] = str(remote_ip)
    request = urllib.request.Request(
        SITEVERIFY_URL, data=urllib.parse.urlencode(fields).encode('ascii'), method='POST',
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'},
    )
    open_url = opener or urllib.request.build_opener(_NoRedirect()).open
    try:
        with open_url(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError('response too large')
        result = json.loads(body)
    except Exception as exc:    # red caída, tiempo agotado, HTTP de error, respuesta que no es JSON...
        logger.warning('turnstile verification unavailable error=%s', type(exc).__name__)
        return False
    return isinstance(result, dict) and result.get('success') is True
