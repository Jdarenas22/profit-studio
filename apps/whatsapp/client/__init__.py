"""Cliente de WhatsApp según el modo (`apps.whatsapp.conf`). Las pruebas lo reemplazan con
`mock.patch('apps.whatsapp.client.get_client', ...)`."""
from django.conf import settings

from .. import conf
from .base import SendResult, WhatsAppAPIError, WhatsAppClient, classify

_simulated_client = None


def get_client():
    """Cliente vigente. `off` (o `live` incompleto) lanza `WhatsAppAPIError('not_configured')`."""
    global _simulated_client
    current = conf.mode()
    if current == conf.MODE_SIMULATED:
        from .fake import FakeWhatsAppClient
        if _simulated_client is None:
            _simulated_client = FakeWhatsAppClient()
        return _simulated_client
    if current == conf.MODE_LIVE and not conf.missing_live_settings():
        from .live import CloudAPIClient
        return CloudAPIClient(
            token=settings.WHATSAPP_CLOUD_ACCESS_TOKEN, phone_number_id=settings.WHATSAPP_CLOUD_PHONE_NUMBER_ID,
            graph_version=settings.WHATSAPP_CLOUD_GRAPH_VERSION, timeout=settings.WHATSAPP_CLOUD_TIMEOUT_SECONDS,
        )
    raise WhatsAppAPIError('not_configured', http_status=401)


__all__ = ['SendResult', 'WhatsAppAPIError', 'WhatsAppClient', 'classify', 'get_client']
