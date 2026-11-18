"""Envío de la cola (contrato, sección 7): reclamo atómico, ventana horaria, reintentos y errores.

- `kick(id)`: intento inmediato en un hilo (o en línea con `WHATSAPP_CLOUD_SEND_INLINE`).
- `process_due()`: lo usa el cron (`process_whatsapp_queue`): recupera envíos colgados y envía lo
  vencido, con tope por corrida y 1 s entre mensajes.
- Antes de enviar se vuelve a comprobar todo (modo, pausas, ventana, tope diario, consentimiento,
  contacto y que el aviso siga teniendo sentido).

Logs: id del mensaje, tipo, estado, intento y código. Nunca teléfono, token ni texto.
"""
import logging
import random
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections, connection
from django.utils import timezone

from . import alerts, catalog, conf
from . import calendar as send_window
from .client import WhatsAppAPIError, get_client
from .client import base as client_base
from .models import (
    KIND_OPTIN_CONFIRM, SERVICE_KINDS, TEMPLATE_KINDS, WhatsAppChannelState, WhatsAppContact, WhatsAppInbound,
    WhatsAppMessage,
)

logger = logging.getLogger(__name__)

BACKOFF_SECONDS = (60, 300, 1800, 7200)
JITTER = 0.2
ECOSYSTEM_WAIT = timedelta(hours=24)
TEMPLATE_PAUSED_WAIT = timedelta(hours=1)
STALE_SENDING = timedelta(minutes=40)
RUN_LIMIT = 50
RUN_BUDGET_SECONDS = 4 * 60 - 20
PAUSE_BETWEEN_SENDS = 1.0
MESSAGE_RETENTION = timedelta(days=180)
INBOUND_RETENTION = timedelta(days=90)
UNDELIVERABLE_LIMIT = 2

_sleep = time.sleep   # las pruebas lo reemplazan

M = WhatsAppMessage


# ─── Arranque ─────────────────────────────────────────────────────────────────

def kick(message_id):
    """Intento inmediato (los avisos por acción). El cron es el respaldo."""
    if not conf.enabled():
        return
    if getattr(settings, 'WHATSAPP_CLOUD_SEND_INLINE', False):
        process_one(message_id)
        return
    thread = threading.Thread(target=_thread_main, args=(message_id,), name=f'whatsapp-{message_id}', daemon=True)
    thread.start()


def _thread_main(message_id):
    close_old_connections()
    try:
        process_one(message_id)
    except Exception as exc:   # noqa: BLE001 — nunca dejar morir el hilo sin registro
        logger.error('whatsapp thread crashed msg=%s error=%s', message_id, type(exc).__name__)
    finally:
        connection.close()


# ─── Comprobaciones ───────────────────────────────────────────────────────────

def _local_day_start(now):
    return timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)


def sent_today(now):
    return M.objects.filter(kind__in=TEMPLATE_KINDS, accepted_at__gte=_local_day_start(now)).count()


def _finish(message, status, code, now, **extra):
    fields = {'status': status, 'error_code': code, 'locked_at': None, **extra}
    if status == M.STATUS_FAILED:
        fields['failed_at'] = now
    M.objects.filter(pk=message.pk).update(**fields)
    logger.info('whatsapp msg=%s kind=%s status=%s code=%s', message.pk, message.kind, status, code or '-')
    return status


def _defer(message, when, code=''):
    M.objects.filter(pk=message.pk, status=M.STATUS_QUEUED).update(next_attempt_at=when, error_code=code)
    return 'deferred'


def _recipient_problem(message, contact, now):
    """Código de cancelación si la persona ya no puede recibir este mensaje, o ''."""
    user = message.recipient
    if message.kind in SERVICE_KINDS:
        if contact is None:
            return 'contact_missing'
        if contact.last_inbound_at is None or now - contact.last_inbound_at > catalog.SERVICE_WINDOW:
            return 'outside_window'
        return ''
    if not user.is_active:
        return 'user_inactive'
    if contact is None:
        return 'contact_missing'
    if message.kind == KIND_OPTIN_CONFIRM:
        if contact.status != WhatsAppContact.STATUS_PENDING or message.subject_id != contact.pk:
            return 'contact_inactive'
        expected = message.dedupe_key.split(':')[2] if message.dedupe_key.count(':') >= 3 else ''
        from .services import _phone_hash
        if expected != _phone_hash(contact.phone_e164):
            return 'phone_changed'
    elif contact.status != WhatsAppContact.STATUS_ACTIVE:
        return 'contact_inactive'
    from .services import consent_valid
    if not consent_valid(user):
        return 'no_consent'
    return ''


def claim(message_id, now):
    """`queued` -> `sending` de forma atómica. True si este proceso lo ganó."""
    current = M.objects.filter(pk=message_id).values_list('attempts', flat=True).first()
    if current is None:
        return False
    return bool(M.objects.filter(pk=message_id, status=M.STATUS_QUEUED, next_attempt_at__lte=now).update(
        status=M.STATUS_SENDING, locked_at=now, attempts=current + 1))


# ─── Envío de un mensaje ──────────────────────────────────────────────────────

def process_one(message_id, now=None, client=None):
    """Intenta enviar un mensaje. Devuelve un texto con el resultado (útil para logs y pruebas)."""
    now = now or timezone.now()
    message = M.objects.select_related('recipient').filter(pk=message_id).first()
    if message is None or message.status != M.STATUS_QUEUED:
        return 'skipped'
    if not conf.sending_allowed():
        return 'blocked'
    if message.next_attempt_at > now:
        return 'not_due'
    if message.expires_at and now > message.expires_at:
        return _finish(message, M.STATUS_EXPIRED, message.error_code or 'expired', now)
    state = WhatsAppChannelState.get()
    if state.paused:
        return 'paused'
    is_template = message.kind in TEMPLATE_KINDS
    if is_template:
        if message.template_name in state.paused_templates:
            return _defer(message, now + TEMPLATE_PAUSED_WAIT, 'template_paused')
        if not send_window.in_window(now):
            return _defer(message, send_window.next_open(now), 'outside_hours')
        limit = int(getattr(settings, 'WHATSAPP_CLOUD_DAILY_LIMIT', 200))
        if sent_today(now) >= limit:
            tomorrow = _local_day_start(now) + timedelta(days=1)
            logger.warning('whatsapp daily limit reached limit=%s', limit)
            return _defer(message, send_window.next_open(tomorrow), 'daily_limit')
    contact = WhatsAppContact.objects.filter(user_id=message.recipient_id).first()
    problem = _recipient_problem(message, contact, now)
    if problem:
        final = M.STATUS_EXPIRED if problem == 'outside_window' else M.STATUS_CANCELLED
        return _finish(message, final, problem, now)
    resolution = catalog.resolve(message, timezone.localdate(now))
    if resolution.cancelled:
        return _finish(message, M.STATUS_CANCELLED, resolution.cancel_code, now)
    if not claim(message.pk, now):
        return 'skipped'
    message.refresh_from_db(fields=['attempts', 'status'])
    try:
        client = client or get_client()
        if is_template:
            spec = catalog.TEMPLATES[message.kind]
            result = client.send_template(
                to=contact.phone_e164, template_name=spec.name,
                language=getattr(settings, 'WHATSAPP_CLOUD_TEMPLATE_LANG', 'es_CO'),
                body_params=resolution.params, button_payloads=spec.quick_replies, ref=message.ref)
        else:
            result = client.send_text(to=contact.phone_e164, text=catalog.service_text(message.kind), ref=message.ref)
    except WhatsAppAPIError as exc:
        return handle_error(message, exc.code, exc.kind, now, meta_code=exc.meta_code,
                            retry_after=exc.retry_after, contact=contact)
    M.objects.filter(pk=message.pk, wamid__isnull=True).update(wamid=result.wamid)
    M.objects.filter(pk=message.pk, status=M.STATUS_SENDING).update(
        status=M.STATUS_ACCEPTED, accepted_at=now, locked_at=None, error_code='')
    contact_updates = {}
    if result.wa_id and result.wa_id != contact.wa_id:
        contact_updates['wa_id'] = result.wa_id[:20]
    if message.kind == KIND_OPTIN_CONFIRM:
        contact_updates['confirmation_sent_at'] = now
    if contact_updates:
        WhatsAppContact.objects.filter(pk=contact.pk).update(**contact_updates)
    logger.info('whatsapp msg=%s kind=%s status=accepted attempt=%s', message.pk, message.kind, message.attempts)
    return M.STATUS_ACCEPTED


def _backoff(attempts, retry_after=None):
    base = BACKOFF_SECONDS[min(max(attempts, 1), len(BACKOFF_SECONDS)) - 1]
    wait = base * random.uniform(1 - JITTER, 1 + JITTER)
    if retry_after:
        wait = max(wait, retry_after)
    return timedelta(seconds=wait)


def _requeue(message, when, code, keep_attempt=True, now=None):
    fields = {'status': M.STATUS_QUEUED, 'locked_at': None, 'next_attempt_at': when, 'error_code': code,
              'wamid': None}
    if not keep_attempt:
        fields['attempts'] = max(0, message.attempts - 1)
    M.objects.filter(pk=message.pk).update(**fields)
    logger.info('whatsapp msg=%s kind=%s status=queued code=%s', message.pk, message.kind, code)
    return 'retry'


def handle_error(message, code, kind, now, meta_code=None, retry_after=None, contact=None):
    """Aplica la tabla 7.3 (lo usan el envío y el webhook de estados)."""
    if message.expires_at and now > message.expires_at and kind in (client_base.TRANSIENT, client_base.ECOSYSTEM):
        return _finish(message, M.STATUS_EXPIRED, code, now)
    if kind == client_base.TRANSIENT:
        if message.attempts >= message.max_attempts:
            return _finish(message, M.STATUS_FAILED, code, now)
        return _requeue(message, now + _backoff(message.attempts, retry_after), code)
    if kind == client_base.ECOSYSTEM:
        if message.error_code == code:      # ya se reintentó una vez por este motivo
            return _finish(message, M.STATUS_FAILED, code, now)
        return _requeue(message, now + ECOSYSTEM_WAIT, code)
    if kind == client_base.WINDOW:
        return _finish(message, M.STATUS_EXPIRED, code, now)
    if kind == client_base.RECIPIENT:
        contact = contact or WhatsAppContact.objects.filter(user_id=message.recipient_id).first()
        if contact is not None:
            if meta_code == 131026:
                count = contact.undeliverable_count + 1
                updates = {'undeliverable_count': count}
                if count >= UNDELIVERABLE_LIMIT and contact.status == WhatsAppContact.STATUS_ACTIVE:
                    updates['status'] = WhatsAppContact.STATUS_UNDELIVERABLE
                WhatsAppContact.objects.filter(pk=contact.pk).update(**updates)
            elif meta_code == 131021:
                WhatsAppContact.objects.filter(pk=contact.pk).update(status=WhatsAppContact.STATUS_WRONG_NUMBER)
        return _finish(message, M.STATUS_FAILED, code, now)
    if kind == client_base.TEMPLATE:
        if message.template_name:
            alerts.pause_template(message.template_name, code, now)
        return _finish(message, M.STATUS_FAILED, code, now)
    if kind == client_base.ACCOUNT:
        alerts.pause_channel(code, now)
        return _requeue(message, now, code, keep_attempt=False)
    return _finish(message, M.STATUS_FAILED, code, now)


# ─── Corrida del cron ─────────────────────────────────────────────────────────

def recover_stale(now=None):
    """`sending` de más de 40 min (el proceso murió a mitad de la llamada y no llegó ningún webhook
    con su `ref`): vuelve a la cola. Así se prefiere no duplicar sin perder avisos."""
    now = now or timezone.now()
    count = M.objects.filter(status=M.STATUS_SENDING, locked_at__lt=now - STALE_SENDING).update(
        status=M.STATUS_QUEUED, locked_at=None, next_attempt_at=now, error_code='worker_lost')
    if count:
        logger.warning('whatsapp requeued stale messages count=%s', count)
    return count


def due_ids(now, limit=RUN_LIMIT):
    return list(M.objects.filter(status=M.STATUS_QUEUED, next_attempt_at__lte=now)
                .order_by('next_attempt_at', 'pk').values_list('pk', flat=True)[:limit])


def process_due(now=None, limit=RUN_LIMIT, client=None):
    """Envía lo vencido. Devuelve un resumen {resultado: cantidad}."""
    started = time.monotonic()
    summary = {}
    for index, message_id in enumerate(due_ids(now or timezone.now(), limit)):
        if time.monotonic() - started > RUN_BUDGET_SECONDS:
            break
        if index:
            _sleep(PAUSE_BETWEEN_SENDS)
        result = process_one(message_id, now=now, client=client)
        summary[result] = summary.get(result, 0) + 1
        if result in ('paused', 'blocked') or WhatsAppChannelState.get().paused:
            break
    return summary


def purge(now=None):
    now = now or timezone.now()
    messages = M.objects.filter(created_at__lt=now - MESSAGE_RETENTION).exclude(
        status__in=(M.STATUS_QUEUED, M.STATUS_SENDING)).delete()[0]
    inbound = WhatsAppInbound.objects.filter(received_at__lt=now - INBOUND_RETENTION).delete()[0]
    return messages, inbound
