"""Casos de uso de los avisos por WhatsApp (contrato, secciones 1, 4 y 10). Sin HTTP.

- Solo la propia usuaria activa o retira sus avisos (las vistas pasan `request.user`).
- Para recibir avisos hacen falta, a la vez: contacto `active` (número confirmado con el botón),
  consentimiento `whatsapp_notices` vigente y usuaria activa.
- Encolar es idempotente por `dedupe_key`. Los ganchos `notify_*` que llaman otras apps NUNCA
  lanzan excepciones: un fallo de WhatsApp no interrumpe la aprobación de un plan ni un pago.
"""
import functools
import hashlib
import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.health import services as health_services
from apps.health.models import PURPOSE_WHATSAPP

from . import catalog, conf
from .models import (
    KIND_AUTOREPLY, KIND_CONFIRM_ACK, KIND_MEMBERSHIP_EXPIRED, KIND_MEMBERSHIP_EXPIRING, KIND_NOT_ME_ACK,
    KIND_OPTIN_CONFIRM, KIND_OPTOUT_ACK, KIND_PAYMENT_APPROVED, KIND_PLAN_APPROVED, KIND_TRAINER_REVIEW,
    SERVICE_KINDS, WhatsAppContact, WhatsAppMessage,
)
from .phone import InvalidPhone, normalize_phone

logger = logging.getLogger(__name__)

RESEND_MIN_INTERVAL = timedelta(minutes=10)
RESEND_DAILY_LIMIT = 3
REVIEW_BUCKET_HOURS = 3
AUTOREPLY_INTERVAL = timedelta(hours=24)
EXPIRED_LOOKBACK_DAYS = 3


class WhatsAppError(Exception):
    """Error de negocio con un mensaje apto para mostrar. `field` = campo del formulario o 'general'."""

    def __init__(self, message, field='general'):
        super().__init__(message)
        self.message = message
        self.field = field


# ─── Consultas ────────────────────────────────────────────────────────────────

def contact_for(user):
    return WhatsAppContact.objects.filter(user=user).first()


def consent_valid(user):
    return health_services.has_valid_consent(user, PURPOSE_WHATSAPP)


def current_text():
    return health_services.current_text(PURPOSE_WHATSAPP)


def can_receive(user, contact=None):
    contact = contact if contact is not None else contact_for(user)
    return bool(user.is_active and contact is not None and contact.status == WhatsAppContact.STATUS_ACTIVE
                and consent_valid(user))


def status_for(user):
    """Estado para las pantallas: 'off' si no hay contacto o se retiró el consentimiento."""
    contact = contact_for(user)
    if contact is None:
        return 'off'
    if contact.status in WhatsAppContact.LIVE_STATUSES and not consent_valid(user):
        return 'off'
    return contact.status


def _phone_hash(phone):
    return hashlib.sha256(phone.encode('utf-8')).hexdigest()[:12]


# ─── Cola ─────────────────────────────────────────────────────────────────────

def enqueue(recipient, kind, dedupe_key, *, subject_type=WhatsAppMessage.SUBJECT_NONE, subject_id=None,
            subject_date=None, now=None, kick=True):
    """Crea el aviso si no existe (idempotente). Devuelve (mensaje, creado)."""
    now = now or timezone.now()
    defaults = {
        'recipient': recipient, 'kind': kind,
        'template_name': catalog.TEMPLATES[kind].name if kind in catalog.TEMPLATES else '',
        'subject_type': subject_type, 'subject_id': subject_id, 'subject_date': subject_date,
        'next_attempt_at': now, 'created_at': now,
        'expires_at': catalog.expires_at_for(kind, now, subject_date),
    }
    try:
        with transaction.atomic():
            message, created = WhatsAppMessage.objects.get_or_create(dedupe_key=dedupe_key, defaults=defaults)
    except IntegrityError:   # carrera entre dos procesos
        message, created = WhatsAppMessage.objects.get(dedupe_key=dedupe_key), False
    if created:
        logger.info('whatsapp queued msg=%s kind=%s', message.pk, kind)
        if kick:
            from . import queue
            transaction.on_commit(lambda: queue.kick(message.pk))
    return message, created


def cancel_queued(user, reason, kinds=None):
    qs = WhatsAppMessage.objects.filter(recipient=user, status=WhatsAppMessage.STATUS_QUEUED)
    if kinds is not None:
        qs = qs.filter(kind__in=kinds)
    return qs.update(status=WhatsAppMessage.STATUS_CANCELLED, error_code=reason)


def _enqueue_optin(contact, now):
    n = WhatsAppMessage.objects.filter(recipient=contact.user, kind=KIND_OPTIN_CONFIRM).count()
    key = f'optin:{contact.pk}:{_phone_hash(contact.phone_e164)}:{n}'
    return enqueue(contact.user, KIND_OPTIN_CONFIRM, key, subject_type=WhatsAppMessage.SUBJECT_CONTACT,
                   subject_id=contact.pk, now=now)


# ─── Alta, reenvío y baja desde la web ────────────────────────────────────────

def enable(user, phone_raw, accepted_version, now=None):
    """Activa los avisos: consentimiento + número pendiente + confirmación por WhatsApp.
    Devuelve el contacto. Lanza `WhatsAppError` con un mensaje para la pantalla."""
    now = now or timezone.now()
    if not conf.sending_allowed():
        raise WhatsAppError('Los avisos por WhatsApp aún no están disponibles.')
    if health_services.is_minor(user):
        raise WhatsAppError('Los avisos por WhatsApp son solo para mayores de 18 años. '
                            'Consulta con tu entrenador.')
    try:
        phone = normalize_phone(phone_raw)
    except InvalidPhone as exc:
        raise WhatsAppError(str(exc), field='phone') from None
    taken_message = 'Ese número ya está registrado para avisos en otra cuenta.'
    if WhatsAppContact.objects.filter(phone_e164=phone, status__in=WhatsAppContact.LIVE_STATUSES) \
            .exclude(user=user).exists():
        raise WhatsAppError(taken_message, field='phone')
    try:
        with transaction.atomic():   # consentimiento y contacto se guardan juntos o nada
            try:
                record, _ = health_services.grant_consent(user, PURPOSE_WHATSAPP, accepted_version)
            except health_services.ConsentError as exc:
                raise WhatsAppError(str(exc), field='accept') from None
            contact =WhatsAppContact.objects.select_for_update().filter(user=user).first()
            if contact is not None and contact.status == WhatsAppContact.STATUS_ACTIVE \
                    and contact.phone_e164 == phone:
                contact.consent = record
                contact.save(update_fields=['consent', 'updated_at'])
                return contact
            if contact is None:
                contact = WhatsAppContact(user=user)
            if contact.phone_e164 != phone:
                contact.wa_id = ''
                contact.bsuid = ''
            contact.phone_e164 = phone
            contact.status = WhatsAppContact.STATUS_PENDING
            contact.consent = record
            contact.confirmed_at = None
            contact.opted_out_at = None
            contact.undeliverable_count = 0
            contact.save()
            cancel_queued(user, 'phone_changed', kinds=[KIND_OPTIN_CONFIRM])
            _enqueue_optin(contact, now)
    except IntegrityError:
        raise WhatsAppError(taken_message, field='phone') from None
    logger.info('whatsapp enabled user_id=%s', user.pk)
    return contact


def resend_confirmation(user, now=None):
    now = now or timezone.now()
    if not conf.sending_allowed():
        raise WhatsAppError('Los avisos por WhatsApp aún no están disponibles.')
    contact = contact_for(user)
    if contact is None or contact.status != WhatsAppContact.STATUS_PENDING or not consent_valid(user):
        raise WhatsAppError('No hay una confirmación pendiente.')
    sent = WhatsAppMessage.objects.filter(recipient=user, kind=KIND_OPTIN_CONFIRM)
    last = sent.order_by('-created_at').first()
    if last is not None and now - last.created_at < RESEND_MIN_INTERVAL:
        raise WhatsAppError('Ya te enviamos la confirmación hace poco. Espera unos minutos.')
    today_start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    if sent.filter(created_at__gte=today_start).count() >= RESEND_DAILY_LIMIT:
        raise WhatsAppError('Llegaste al máximo de reenvíos de hoy. Inténtalo mañana.')
    cancel_queued(user, 'resent', kinds=[KIND_OPTIN_CONFIRM])
    message, _ = _enqueue_optin(contact, now)
    return message


def can_resend(user, now=None):
    now = now or timezone.now()
    contact = contact_for(user)
    if contact is None or contact.status != WhatsAppContact.STATUS_PENDING:
        return False
    last = WhatsAppMessage.objects.filter(recipient=user, kind=KIND_OPTIN_CONFIRM).order_by('-created_at').first()
    return last is None or now - last.created_at >= RESEND_MIN_INTERVAL


def _opt_out(contact, now, reason):
    user = contact.user
    health_services.revoke_consent(user, PURPOSE_WHATSAPP)
    WhatsAppContact.objects.filter(pk=contact.pk).update(
        status=WhatsAppContact.STATUS_OPTED_OUT, opted_out_at=now, updated_at=now)
    contact.status = WhatsAppContact.STATUS_OPTED_OUT
    contact.opted_out_at = now
    cancel_queued(user, reason, kinds=[k for k, _ in WhatsAppMessage._meta.get_field('kind').choices
                                       if k not in SERVICE_KINDS])
    logger.info('whatsapp opted out user_id=%s via=%s', user.pk, reason)


def disable(user, now=None):
    """Baja desde la web. Siempre permitida (derecho de la titular), aunque el modo esté apagado."""
    now = now or timezone.now()
    contact = contact_for(user)
    revoked = health_services.revoke_consent(user, PURPOSE_WHATSAPP)
    if contact is None:
        return bool(revoked)
    with transaction.atomic():
        _opt_out(contact, now, 'optout_web')
    return True


# ─── Respuestas recibidas por WhatsApp (las llama el webhook) ─────────────────

def _ack(contact, kind, key, now):
    return enqueue(contact.user, kind, key, subject_type=WhatsAppMessage.SUBJECT_CONTACT,
                   subject_id=contact.pk, now=now)


def inbound_confirm(contact, inbound_wamid, now):
    if contact.status != WhatsAppContact.STATUS_PENDING:
        return False
    WhatsAppContact.objects.filter(pk=contact.pk).update(
        status=WhatsAppContact.STATUS_ACTIVE, confirmed_at=now, updated_at=now)
    contact.status = WhatsAppContact.STATUS_ACTIVE
    _ack(contact, KIND_CONFIRM_ACK, f'ack:{inbound_wamid}', now)
    logger.info('whatsapp confirmed contact=%s', contact.pk)
    return True


def inbound_not_me(contact, inbound_wamid, now):
    if contact.status not in WhatsAppContact.LIVE_STATUSES:
        return False
    WhatsAppContact.objects.filter(pk=contact.pk).update(status=WhatsAppContact.STATUS_WRONG_NUMBER, updated_at=now)
    contact.status = WhatsAppContact.STATUS_WRONG_NUMBER
    cancel_queued(contact.user, 'wrong_number',
                  kinds=[k for k, _ in WhatsAppMessage._meta.get_field('kind').choices if k not in SERVICE_KINDS])
    _ack(contact, KIND_NOT_ME_ACK, f'ack:{inbound_wamid}', now)
    logger.info('whatsapp wrong number contact=%s', contact.pk)
    return True


def inbound_optout(contact, inbound_wamid, now):
    already = contact.status == WhatsAppContact.STATUS_OPTED_OUT
    _opt_out(contact, now, 'optout_whatsapp')
    if not already:
        _ack(contact, KIND_OPTOUT_ACK, f'ack:{inbound_wamid}', now)
    return True


def inbound_other(contact, now):
    """Respuesta automática, máximo una cada 24 h por persona."""
    last = contact.last_autoreply_at
    if last is not None and now - last < AUTOREPLY_INTERVAL:
        return False
    WhatsAppContact.objects.filter(pk=contact.pk).update(last_autoreply_at=now)
    contact.last_autoreply_at = now
    _ack(contact, KIND_AUTOREPLY, f'auto:{contact.pk}:{timezone.localtime(now):%Y%m%d%H%M}', now)
    return True


# ─── Avisos del negocio ───────────────────────────────────────────────────────

def _enqueue_if_allowed(kind, recipient, dedupe_key, **subject):
    """(mensaje, creado) o (None, False) si la persona no puede recibirlo hoy (no crea nada)."""
    if not conf.sending_allowed() or not can_receive(recipient):
        return None, False
    return enqueue(recipient, kind, dedupe_key, **subject)


def enqueue_notice(kind, recipient, dedupe_key, **subject):
    """Encola un aviso solo si la persona puede recibirlo hoy. Devuelve el mensaje o None."""
    return _enqueue_if_allowed(kind, recipient, dedupe_key, **subject)[0]


def _never_raise(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            if not conf.enabled():
                return None
            return func(*args, **kwargs)
        except Exception as exc:   # noqa: BLE001 — WhatsApp nunca interrumpe el flujo
            logger.warning('whatsapp hook failed hook=%s error=%s', func.__name__, type(exc).__name__)
            return None
    return wrapper


def trainer_recipients(client):
    """Misma regla que los correos: el entrenador asignado o, si no tiene, las superusuarias."""
    trainer = client.assigned_trainer
    if trainer is not None and trainer.is_active:
        return [trainer]
    return list(get_user_model().objects.filter(role='trainer', is_superuser=True, is_active=True))


@_never_raise
def notify_plan_approved(plan):
    return enqueue_notice(KIND_PLAN_APPROVED, plan.client, f'plan:{plan.pk}',
                          subject_type=WhatsAppMessage.SUBJECT_PLAN, subject_id=plan.pk)


@_never_raise
def notify_trainer_review(client, now=None):
    """Agrupado: máximo un aviso por entrenador cada 3 horas."""
    now = timezone.localtime(now or timezone.now())
    bucket = now.hour // REVIEW_BUCKET_HOURS
    created = []
    for trainer in trainer_recipients(client):
        message = enqueue_notice(KIND_TRAINER_REVIEW, trainer, f'review:{trainer.pk}:{now:%Y%m%d}:{bucket}')
        if message is not None:
            created.append(message)
    return created


@_never_raise
def notify_payment_approved(payment_id):
    from django.conf import settings
    from apps.payments.models import Payment
    if not getattr(settings, 'WHATSAPP_CLOUD_PAYMENT_NOTICE', False):
        return None
    payment = Payment.objects.select_related('user').filter(pk=payment_id).first()
    if payment is None or payment.user is None:
        return None
    return enqueue_notice(KIND_PAYMENT_APPROVED, payment.user, f'pay:{payment.pk}',
                          subject_type=WhatsAppMessage.SUBJECT_PAYMENT, subject_id=payment.pk)


def enqueue_membership_notices(today=None, now=None):
    """Avisos de membresía del día (lo corre el cron; idempotente). Devuelve cuántos creó."""
    from django.conf import settings
    from apps.memberships.models import Membership
    if not conf.sending_allowed():
        return 0
    today = today or timezone.localdate()
    days = max(0, int(getattr(settings, 'WHATSAPP_CLOUD_EXPIRING_DAYS', 3)))
    base = Membership.objects.select_related('user').filter(
        is_active=True, user__role='member', user__is_active=True,
        user__whatsapp_contact__status=WhatsAppContact.STATUS_ACTIVE)
    created = 0
    expiring = base.filter(end_date__gte=today, end_date__lte=today + timedelta(days=days))
    for membership in expiring:
        if _enqueue_if_allowed(KIND_MEMBERSHIP_EXPIRING, membership.user, f'mexp:{membership.pk}:{membership.end_date}',
                          subject_type=WhatsAppMessage.SUBJECT_MEMBERSHIP, subject_id=membership.pk,
                          subject_date=membership.end_date, now=now)[1]:
            created += 1
    expired = base.filter(end_date__lt=today, end_date__gte=today - timedelta(days=EXPIRED_LOOKBACK_DAYS))
    for membership in expired:
        if _enqueue_if_allowed(KIND_MEMBERSHIP_EXPIRED, membership.user, f'mend:{membership.pk}:{membership.end_date}',
                          subject_type=WhatsAppMessage.SUBJECT_MEMBERSHIP, subject_id=membership.pk,
                          subject_date=membership.end_date, now=now)[1]:
            created += 1
    return created


# ─── Pantalla de la usuaria ───────────────────────────────────────────────────

def recent_for(user, limit=10):
    messages = WhatsAppMessage.objects.filter(recipient=user).exclude(kind__in=SERVICE_KINDS)[:limit]
    return [catalog.describe(m) for m in messages]
