"""Cliente SIMULADO (modo `simulated` y pruebas): nunca sale a la red.

Guarda las llamadas en memoria (para las pruebas) y devuelve un `wamid` falso. `fail_with` permite
forzar un error de Meta en las pruebas.
"""
import logging
import uuid

from .base import SendResult, WhatsAppAPIError, WhatsAppClient

logger = logging.getLogger('apps.whatsapp')


class FakeWhatsAppClient(WhatsAppClient):
    is_simulated = True

    def __init__(self, fail_with=None):
        self.calls = []
        self.fail_with = fail_with   # WhatsAppAPIError o lista de ellos (uno por llamada)

    def _result(self, kind, **data):
        self.calls.append({'kind': kind, **data})
        error = self.fail_with
        if isinstance(error, list):
            error = error.pop(0) if error else None
        if isinstance(error, WhatsAppAPIError):
            raise error
        wamid = f'wamid.SIMULATED-{uuid.uuid4().hex}'
        logger.info('whatsapp simulated send kind=%s', kind)
        return SendResult(wamid=wamid, wa_id=str(data.get('to', '')).lstrip('+'))

    def send_template(self, *, to, template_name, language, body_params, button_payloads, ref):
        return self._result('template', to=to, template_name=template_name, language=language,
                            body_params=list(body_params), button_payloads=list(button_payloads), ref=str(ref))

    def send_text(self, *, to, text, ref):
        return self._result('text', to=to, text=text, ref=str(ref))
