"""Plantillas, parámetros y textos de servicio (contrato, sección 3).

REGLA: cero datos de salud. Los parámetros solo pueden salir de `User.first_name`,
`Membership.end_date` y el estado del objeto del aviso; nada de `apps.health` ni del contenido de
`apps.plans`. Los parámetros se arman AL ENVIAR (nunca se guardan) y, si el aviso ya no tiene
sentido (membresía renovada, plan retirado), se cancela.

Orden de los botones al crear cada plantilla en Meta (el índice importa):
- `profit_confirmar_numero_v1`: 0 = respuesta rápida "Sí, soy yo", 1 = respuesta rápida "No soy yo".
- Las demás: 0 = respuesta rápida "No recibir avisos", 1 = botón de URL (fija, al dominio definitivo).
"""
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

from . import conf
from .models import (
    KIND_AUTOREPLY, KIND_CONFIRM_ACK, KIND_MEMBERSHIP_EXPIRED, KIND_MEMBERSHIP_EXPIRING, KIND_NOT_ME_ACK,
    KIND_OPTIN_CONFIRM, KIND_OPTOUT_ACK, KIND_PAYMENT_APPROVED, KIND_PLAN_APPROVED, KIND_TRAINER_REVIEW,
    WhatsAppMessage,
)

PAYLOAD_CONFIRM = 'confirm'
PAYLOAD_NOT_ME = 'not_me'
PAYLOAD_OPTOUT = 'optout'

# Texto normalizado (`normalize_text`) de los botones de respuesta rápida, por si Meta devuelve el
# texto del botón en vez de la carga
BUTTON_TEXTS = {
    'si soy yo': PAYLOAD_CONFIRM,
    'no soy yo': PAYLOAD_NOT_ME,
    'no recibir avisos': PAYLOAD_OPTOUT,
}


@dataclass(frozen=True)
class TemplateSpec:
    name: str
    params: tuple = ()
    quick_replies: tuple = ()          # ((índice, carga), ...)
    fallback_name: str = 'clienta'


_OPTOUT_BUTTON = ((0, PAYLOAD_OPTOUT),)

TEMPLATES = {
    KIND_OPTIN_CONFIRM: TemplateSpec('profit_confirmar_numero_v1', (), ((0, PAYLOAD_CONFIRM), (1, PAYLOAD_NOT_ME))),
    KIND_MEMBERSHIP_EXPIRING: TemplateSpec('profit_membresia_por_vencer_v1', ('nombre', 'fecha'), _OPTOUT_BUTTON),
    KIND_MEMBERSHIP_EXPIRED: TemplateSpec('profit_membresia_vencida_v1', ('nombre', 'fecha'), _OPTOUT_BUTTON),
    KIND_PLAN_APPROVED: TemplateSpec('profit_plan_aprobado_v1', ('nombre',), _OPTOUT_BUTTON),
    KIND_TRAINER_REVIEW: TemplateSpec('profit_pendientes_revision_v1', ('nombre',), _OPTOUT_BUTTON, 'equipo'),
    KIND_PAYMENT_APPROVED: TemplateSpec('profit_pago_recibido_v1', ('nombre', 'fecha'), _OPTOUT_BUTTON),
}

# Cuánto tiempo tiene sentido intentar cada aviso
EXPIRY = {
    KIND_OPTIN_CONFIRM: timedelta(days=7),
    KIND_MEMBERSHIP_EXPIRED: timedelta(days=7),
    KIND_PLAN_APPROVED: timedelta(days=7),
    KIND_TRAINER_REVIEW: timedelta(days=2),
    KIND_PAYMENT_APPROVED: timedelta(days=3),
}
SERVICE_WINDOW = timedelta(hours=24)

MONTHS = ('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre',
          'octubre', 'noviembre', 'diciembre')
_SPACES = re.compile(r'\s+')
MAX_NAME = 30


def spanish_date(day):
    """`date(2026, 12, 15)` -> `15 de diciembre de 2026` (sin depender del locale del servidor)."""
    return f'{day.day} de {MONTHS[day.month - 1]} de {day.year}'


def clean_name(value, fallback):
    """Primer nombre sin saltos de línea, tabulaciones ni caracteres de control; máximo 30."""
    text = unicodedata.normalize('NFC', str(value or ''))
    text = ''.join(ch if unicodedata.category(ch)[0] != 'C' else ' ' for ch in text)
    text = _SPACES.sub(' ', text).strip()
    first = text.split(' ')[0] if text else ''
    return first[:MAX_NAME] or fallback


def normalize_text(value):
    """Minúsculas, sin tildes ni signos, espacios colapsados (para reconocer BAJA/STOP)."""
    text = unicodedata.normalize('NFKD', str(value or '').lower())
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r'[^a-z0-9ñ ]+', ' ', text)
    return _SPACES.sub(' ', text).strip()


OPTOUT_WORDS = frozenset({'baja', 'stop', 'parar', 'detener', 'cancelar', 'no mas', 'salir', 'desuscribir',
                          'no quiero mas avisos'})
MAX_OPTOUT_LENGTH = 20


def is_optout_text(value):
    text = normalize_text(value)
    return len(text) <= MAX_OPTOUT_LENGTH and text in OPTOUT_WORDS


def settings_url():
    site = conf.site_url()
    return f'{site}/accounts/whatsapp/' if site else ''


def service_text(kind):
    link = settings_url()
    if kind == KIND_OPTOUT_ACK:
        tail = f': {link}' if link else '.'
        return ('Listo. No te enviaremos más avisos de ProFit Studio por WhatsApp. '
                f'Puedes activarlos de nuevo desde tu cuenta{tail}')
    if kind == KIND_CONFIRM_ACK:
        return 'Gracias. Desde ahora recibirás aquí los avisos de tu cuenta de ProFit Studio.'
    if kind == KIND_NOT_ME_ACK:
        return 'Gracias por avisar. No volveremos a escribir a este número.'
    if kind == KIND_AUTOREPLY:
        support = getattr(settings, 'WHATSAPP_LINK', '') or ''
        contact = f' escríbenos a {support}' if support else ' escríbenos por el canal de atención'
        return ('Este número solo envía avisos automáticos de ProFit Studio y no lee respuestas. '
                f'Para hablar con nosotros{contact}. Para dejar de recibir avisos responde BAJA.')
    raise ValueError(kind)


def expires_at_for(kind, now, subject_date=None):
    if kind == KIND_MEMBERSHIP_EXPIRING and subject_date is not None:
        # Después del día de vencimiento ya no tiene sentido avisar que "vence pronto"
        return datetime.combine(subject_date, time(23, 59), tzinfo=timezone.get_current_timezone())
    if kind in EXPIRY:
        return now + EXPIRY[kind]
    return now + SERVICE_WINDOW


# ─── Resolución al enviar ─────────────────────────────────────────────────────

@dataclass
class Resolution:
    cancel_code: str = ''
    params: list = field(default_factory=list)

    @property
    def cancelled(self):
        return bool(self.cancel_code)


def _membership(message):
    from apps.memberships.models import Membership
    return Membership.objects.filter(pk=message.subject_id, user_id=message.recipient_id).first()


def resolve(message, today=None):
    """Comprueba que el aviso siga teniendo sentido y arma sus parámetros (lista de (nombre, valor))."""
    today = today or timezone.localdate()
    kind = message.kind
    spec = TEMPLATES.get(kind)
    if spec is None:
        return Resolution()       # mensajes de servicio: sin parámetros
    name = clean_name(message.recipient.first_name, spec.fallback_name)
    if kind == KIND_OPTIN_CONFIRM:
        return Resolution()
    if kind in (KIND_MEMBERSHIP_EXPIRING, KIND_MEMBERSHIP_EXPIRED):
        membership = _membership(message)
        if membership is None or not membership.is_active or membership.end_date != message.subject_date:
            return Resolution('subject_changed')
        if kind == KIND_MEMBERSHIP_EXPIRING and membership.end_date < today:
            return Resolution('subject_changed')
        if kind == KIND_MEMBERSHIP_EXPIRED and membership.end_date >= today:
            return Resolution('subject_changed')
        return Resolution(params=[('nombre', name), ('fecha', spanish_date(membership.end_date))])
    if kind == KIND_PLAN_APPROVED:
        from apps.plans.models import Plan
        plan = Plan.objects.filter(pk=message.subject_id, client_id=message.recipient_id).only('status').first()
        if plan is None or plan.status != Plan.STATUS_APPROVED:
            return Resolution('subject_changed')
        return Resolution(params=[('nombre', name)])
    if kind == KIND_TRAINER_REVIEW:
        if not message.recipient.is_trainer:
            return Resolution('subject_changed')
        return Resolution(params=[('nombre', name)])
    if kind == KIND_PAYMENT_APPROVED:
        from apps.memberships.models import Membership
        from apps.payments.models import Payment
        payment = Payment.objects.filter(pk=message.subject_id, user_id=message.recipient_id).first()
        membership = Membership.objects.filter(user_id=message.recipient_id).first()
        if payment is None or payment.status != Payment.STATUS_APPROVED or membership is None \
                or not membership.is_valid:
            return Resolution('subject_changed')
        return Resolution(params=[('nombre', name), ('fecha', spanish_date(membership.end_date))])
    return Resolution('unknown_kind')


def describe(message):
    """Para la pantalla de la usuaria: tipo y estado en español, sin contenido."""
    return {
        'kind_label': message.get_kind_display(),
        'created_at': message.created_at,
        'status_label': message.get_status_display(),
        'status': message.status,
    }


__all__ = ['TEMPLATES', 'resolve', 'service_text', 'is_optout_text', 'spanish_date', 'clean_name',
           'WhatsAppMessage']
