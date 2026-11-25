"""Envío de correo por la API HTTPS de Brevo (sin SMTP).

Railway bloquea el SMTP saliente en los planes sin Pro: la conexión a Gmail se queda colgada y gunicorn mata el
proceso. Una llamada HTTPS al puerto 443 no se bloquea. Solo usa la biblioteca estándar (sin dependencias nuevas).

- La clave (`BREVO_API_KEY`) va en la cabecera `api-key`; jamás en la URL, en un mensaje de error ni en el log.
- Los errores salen como `OSError` con solo un código (`brevo_http_401`, `brevo_unreachable`...): así los llamadores
  que ya capturan `OSError` (`email_verification._send`) siguen funcionando igual que con SMTP.
- El remitente tiene que estar verificado en Brevo (Senders & IP): una dirección Gmail basta, sin dominio propio.
"""
import json
import logging
from email.utils import parseaddr
from urllib import error, request

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)

API_ROOT = 'https://api.brevo.com/v3'


class BrevoError(OSError):
    """Fallo de la API de Brevo; el texto es solo un código, nunca datos del mensaje ni la clave."""


def _address(value):
    name, email = parseaddr(value)
    item = {'email': email}
    if name:
        item['name'] = name
    return item


class BrevoEmailBackend(BaseEmailBackend):
    def __init__(self, fail_silently=False, timeout=None, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.timeout = timeout or getattr(settings, 'EMAIL_TIMEOUT', None) or 10
        self.api_key = getattr(settings, 'BREVO_API_KEY', '')

    # ── Llamada HTTP ──
    def _call(self, method, path, payload=None):
        if not self.api_key:
            raise BrevoError('brevo_no_key')
        data = json.dumps(payload).encode('utf-8') if payload is not None else None
        req = request.Request(API_ROOT + path, data=data, method=method, headers={
            'api-key': self.api_key, 'accept': 'application/json', 'content-type': 'application/json'})
        try:
            with request.urlopen(req, timeout=self.timeout) as response:   # noqa: S310 — URL fija https
                return response.status
        except error.HTTPError as exc:
            raise BrevoError(f'brevo_http_{exc.code}') from None
        except (error.URLError, TimeoutError, OSError) as exc:
            raise BrevoError('brevo_timeout' if isinstance(exc, TimeoutError) else 'brevo_unreachable') from None

    def verify(self):
        """Comprueba la clave con GET /account: no envía nada (lo usa `check_infra --smtp`)."""
        self._call('GET', '/account')

    # ── Envío ──
    def _payload(self, message):
        payload = {
            'sender': _address(message.from_email or getattr(settings, 'DEFAULT_FROM_EMAIL', '')),
            'to': [_address(a) for a in message.to],
            'subject': message.subject,
        }
        if message.cc:
            payload['cc'] = [_address(a) for a in message.cc]
        if message.bcc:
            payload['bcc'] = [_address(a) for a in message.bcc]
        if message.reply_to:
            payload['replyTo'] = _address(message.reply_to[0])
        html = next((c for c, mime in getattr(message, 'alternatives', []) if mime == 'text/html'), None)
        if html:
            payload['htmlContent'] = html
        payload['textContent'] = message.body or ' '
        return payload

    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            if not message.recipients():
                continue
            try:
                self._call('POST', '/smtp/email', self._payload(message))
            except BrevoError as exc:
                logger.warning('brevo send failed code=%s', exc)
                if not self.fail_silently:
                    raise
                continue
            sent += 1
        return sent
