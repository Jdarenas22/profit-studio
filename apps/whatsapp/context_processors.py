from . import conf


def whatsapp_flags(request):
    """`WHATSAPP_NOTICES_ENABLED` en las plantillas (enlace "Avisos por WhatsApp"). No toca la base."""
    return {'WHATSAPP_NOTICES_ENABLED': conf.enabled()}
