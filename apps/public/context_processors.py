from django.conf import settings


def site_settings(request):
    return {
        'WHATSAPP_LINK': settings.WHATSAPP_LINK,
        'WHATSAPP_NUMBER': settings.WHATSAPP_NUMBER,
        'INSTAGRAM_URL': settings.INSTAGRAM_URL,
    }
