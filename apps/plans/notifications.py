"""Avisos por correo de los planes (contrato, sección 8.2).

Los correos NUNCA llevan datos de salud, banderas ni contenido del plan: solo "hay algo para
revisar" y dónde verlo. Un fallo del correo se registra y no interrumpe nada.
Destinatario del entrenador: el asignado a la clienta o, si no tiene, las superusuarias.
"""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.urls import reverse

logger = logging.getLogger(__name__)


def _trainer_recipients(client):
    trainer = client.assigned_trainer
    if trainer is not None and trainer.is_active and trainer.email:
        return [trainer.email]
    return list(get_user_model().objects.filter(
        role='trainer', is_superuser=True, is_active=True).exclude(email='').values_list('email', flat=True))


def _send(subject, body, recipients, what):
    if not recipients:
        return False
    try:
        send_mail(subject, body, getattr(settings, 'DEFAULT_FROM_EMAIL', None), recipients, fail_silently=False)
    except Exception as exc:   # el correo nunca interrumpe el flujo
        logger.warning('plan notification failed what=%s error=%s', what, type(exc).__name__)
        return False
    return True


def trainer_draft_ready(plan):
    body = ('Hay un borrador de plan generado con IA para revisar.\n\n'
            f'Ábrelo en el panel: Planes por revisar ({reverse("trainer_plan_inbox")}).\n\n'
            'La clienta no verá nada hasta que lo apruebes.')
    return _send('ProFit Studio: hay un borrador para revisar', body, _trainer_recipients(plan.client), 'draft')


def trainer_job_blocked(job):
    body = ('Una clienta pidió un plan con IA y no se generó por seguridad (su ficha requiere '
            'valoración profesional o no hay ejercicios compatibles).\n\n'
            f'Revisa el detalle en el panel: Planes por revisar ({reverse("trainer_plan_inbox")}). '
            'Puedes armar su plan a mano.')
    return _send('ProFit Studio: un pedido de plan necesita tu atención', body,
                 _trainer_recipients(job.client), 'blocked')


def trainer_job_failed(job):
    body = ('No se pudo generar un borrador de plan con IA.\n\n'
            f'Revisa el detalle en el panel: Planes por revisar ({reverse("trainer_plan_inbox")}).')
    return _send('ProFit Studio: no se pudo generar un plan', body, _trainer_recipients(job.client), 'failed')


def client_plan_approved(plan):
    client = plan.client
    if not client.email:
        return False
    body = ('Tu entrenador revisó y aprobó tu plan.\n\n'
            f'Míralo al iniciar sesión, en Mi plan ({reverse("member_plans")}).')
    return _send('ProFit Studio: tu plan está listo', body, [client.email], 'approved')
