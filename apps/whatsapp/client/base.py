"""Interfaz del cliente de WhatsApp y clasificación de errores de Meta (contrato, secciones 7.2 y 7.3).

Los errores solo llevan códigos (`meta_131026`, `http_503`, `timeout`): nunca el texto de Meta, que
puede repetir el teléfono.
"""
from dataclasses import dataclass

# Clases de error
TRANSIENT = 'transient'
ECOSYSTEM = 'ecosystem'
RECIPIENT = 'recipient'
WINDOW = 'window'
TEMPLATE = 'template'
ACCOUNT = 'account'
UNKNOWN = 'unknown'

_TRANSIENT_CODES = {131000, 131016, 130429, 131056}
_ECOSYSTEM_CODES = {131049}
_RECIPIENT_CODES = {131026, 131021, 131050}
_WINDOW_CODES = {131047}
_TEMPLATE_CODES = {132000, 132001, 132005, 132007, 132012, 132015, 132016, 100, 131008, 131009, 135000}
_ACCOUNT_CODES = {0, 3, 10, 190, 131005, 368, 131031, 131042, 131045, 131048, 131057, 133010}


def classify(meta_code=None, http_status=None):
    """Clase de un error a partir del código de Meta (si hay) o del estado HTTP."""
    if meta_code is not None:
        if meta_code in _TRANSIENT_CODES:
            return TRANSIENT
        if meta_code in _ECOSYSTEM_CODES:
            return ECOSYSTEM
        if meta_code in _RECIPIENT_CODES:
            return RECIPIENT
        if meta_code in _WINDOW_CODES:
            return WINDOW
        if meta_code in _TEMPLATE_CODES:
            return TEMPLATE
        if meta_code in _ACCOUNT_CODES or 200 <= meta_code <= 299:
            return ACCOUNT
        return UNKNOWN
    if http_status is None:          # sin respuesta: red o tiempo agotado
        return TRANSIENT
    if http_status == 429 or http_status >= 500:
        return TRANSIENT
    if http_status in (401, 403):
        return ACCOUNT
    return UNKNOWN


class WhatsAppAPIError(Exception):
    """Error de la API. `code` es el código corto que se guarda (`meta_190`, `http_503`, `timeout`)."""

    def __init__(self, code, meta_code=None, http_status=None, retry_after=None):
        super().__init__(code)
        self.code = code
        self.meta_code = meta_code
        self.http_status = http_status
        self.retry_after = retry_after
        self.kind = classify(meta_code, http_status)


@dataclass(frozen=True)
class SendResult:
    wamid: str
    wa_id: str = ''


class WhatsAppClient:
    """Interfaz. `send_template` y `send_text` devuelven `SendResult` o lanzan `WhatsAppAPIError`."""
    is_simulated = False

    def send_template(self, *, to, template_name, language, body_params, button_payloads, ref):
        raise NotImplementedError

    def send_text(self, *, to, text, ref):
        raise NotImplementedError
