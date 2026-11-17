"""Pausas del canal y alertas a la superusuaria (contrato, sección 7.3).

La alerta es un correo con SOLO el código del motivo, máximo uno cada 24 h por motivo. Un fallo del
correo se registra y no interrumpe nada.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import WhatsAppChannelState

logger = logging.getLogger(__name__)

ALERT_INTERVAL = timedelta(hours=24)


def _alert(state, reason, body, now):
    if state.last_alert_reason == reason and state.last_alert_at and now - state.last_alert_at < ALERT_INTERVAL:
        return False
    state.last_alert_reason = reason
    state.last_alert_at = now
    state.save(update_fields=['last_alert_reason', 'last_alert_at'])
    recipients = list(get_user_model().objects.filter(
        role='trainer', is_superuser=True, is_active=True).exclude(email='').values_list('email', flat=True))
    if not recipients:
        return False
    try:
        send_mail('ProFit Studio: los avisos por WhatsApp necesitan atención', body,
                  getattr(settings, 'DEFAULT_FROM_EMAIL', None), recipients, fail_silently=False)
    except Exception as exc:   # noqa: BLE001
        logger.warning('whatsapp alert email failed error=%s', type(exc).__name__)
        return False
    return True


def pause_channel(code, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        state = WhatsAppChannelState.objects.select_for_update().get_or_create(pk=1)[0]
        if not state.paused:
            state.paused = True
            state.paused_reason = code
            state.paused_at = now
            state.save(update_fields=['paused', 'paused_reason', 'paused_at'])
            logger.error('whatsapp channel paused code=%s', code)
        _alert(state, f'channel:{code}', (
            f'Los avisos por WhatsApp están pausados (código {code}). La cola se conserva.\n\n'
            'Revisa la sección 13 de docs/contracts/whatsapp.md (token, medio de pago o estado de la cuenta en '
            'Meta) y luego ejecuta: python manage.py check_whatsapp --resume'), now)


def pause_template(template_name, code, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        state = WhatsAppChannelState.objects.select_for_update().get_or_create(pk=1)[0]
        if template_name not in state.paused_templates:
            state.paused_templates = [*state.paused_templates, template_name]
            state.save(update_fields=['paused_templates'])
            logger.error('whatsapp template paused template=%s code=%s', template_name, code)
        _alert(state, f'template:{template_name}:{code}', (
            f'La plantilla {template_name} de WhatsApp está pausada (código {code}): ese aviso no se envía.\n\n'
            'Revisa en el Administrador de WhatsApp que esté aprobada, en Spanish (COL) y con los mismos '
            'parámetros. Luego ejecuta: python manage.py check_whatsapp --resume'), now)


def resume():
    state = WhatsAppChannelState.get()
    state.paused = False
    state.paused_reason = ''
    state.paused_at = None
    state.paused_templates = []
    state.save(update_fields=['paused', 'paused_reason', 'paused_at', 'paused_templates'])
    logger.info('whatsapp channel resumed')
    return state
