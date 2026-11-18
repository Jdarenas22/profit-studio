"""Interpretación del webhook de Meta (contrato, sección 6), sin HTTP.

- `valid_signature`: HMAC-SHA256 del cuerpo CRUDO con el app secret, comparado en tiempo constante.
- `process_payload`: aplica estados (`statuses[]`) y respuestas (`messages[]`) de forma idempotente.
  Solo acepta entradas de nuestra cuenta (`WABA_ID`) y de nuestro número (`PHONE_NUMBER_ID`).
- Nunca guarda ni registra el cuerpo, el texto de los mensajes ni teléfonos completos.
"""
import hashlib
import hmac
import logging
import re
import uuid
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import catalog, queue, services
from .client import base as client_base
from .models import WhatsAppContact, WhatsAppInbound, WhatsAppMessage
from .phone import from_wa_id

logger = logging.getLogger(__name__)

M = WhatsAppMessage
SIGNATURE_PREFIX = 'sha256='
CHALLENGE_RE = re.compile(r'^[A-Za-z0-9_-]{1,128}$')
STATUS_MAP = {'sent': M.STATUS_SENT, 'delivered': M.STATUS_DELIVERED, 'read': M.STATUS_READ,
              'played': M.STATUS_READ}


def valid_signature(raw_body, header, secret):
    if not secret or not header or not isinstance(header, str) or not header.startswith(SIGNATURE_PREFIX):
        return False
    expected = hmac.new(secret.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len(SIGNATURE_PREFIX):].strip().lower())


def valid_challenge(value):
    return bool(value) and bool(CHALLENGE_RE.match(value))


def _ts(value, default):
    try:
        return datetime.fromtimestamp(int(value), tz=dt_timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return default


def _str(value, limit):
    return value[:limit] if isinstance(value, str) else ''


# ─── Estados de lo que enviamos ───────────────────────────────────────────────

def _find_message(status):
    wamid = _str(status.get('id'), 128)
    if wamid:
        message = M.objects.filter(wamid=wamid).first()
        if message is not None:
            return message
    ref = _str(status.get('biz_opaque_callback_data'), 64)
    if ref:
        try:
            ref_uuid = uuid.UUID(ref)
        except ValueError:
            return None
        # Reconciliación: el proceso murió antes de guardar el wamid (sigue en `sending`)
        message = M.objects.filter(ref=ref_uuid, status=M.STATUS_SENDING).first()
        if message is not None and wamid:
            M.objects.filter(pk=message.pk, wamid__isnull=True).update(wamid=wamid)
            message.wamid = wamid
        return message
    return None


def apply_status(status, now):
    if not isinstance(status, dict):
        return 'ignored'
    message = _find_message(status)
    if message is None:
        return 'unknown'
    value = _str(status.get('status'), 20)
    when = _ts(status.get('timestamp'), now)
    bsuid = _str(status.get('recipient_user_id'), 160)
    if bsuid:
        WhatsAppContact.objects.filter(user_id=message.recipient_id).exclude(bsuid=bsuid).update(bsuid=bsuid)
    pricing = status.get('pricing') if isinstance(status.get('pricing'), dict) else {}
    if value == 'failed':
        if message.status not in (M.STATUS_SENDING, M.STATUS_ACCEPTED, M.STATUS_SENT):
            return 'stale'
        errors = status.get('errors') if isinstance(status.get('errors'), list) else []
        meta_code = None
        if errors and isinstance(errors[0], dict):
            try:
                meta_code = int(errors[0].get('code'))
            except (TypeError, ValueError):
                meta_code = None
        code = f'meta_{meta_code}' if meta_code is not None else 'meta_unknown'
        return queue.handle_error(message, code, client_base.classify(meta_code, 400 if meta_code is None else None),
                                  now, meta_code=meta_code)
    new_status = STATUS_MAP.get(value)
    if new_status is None:
        return 'ignored'
    current_rank = M.PROGRESS.get(message.status, 0 if message.status == M.STATUS_SENDING else -1)
    updates = {}
    field = {M.STATUS_SENT: 'sent_at', M.STATUS_DELIVERED: 'delivered_at', M.STATUS_READ: 'read_at'}[new_status]
    updates[field] = when
    if pricing:
        updates['pricing_category'] = _str(pricing.get('category'), 20)
        updates['pricing_type'] = _str(pricing.get('type'), 30)
    if current_rank >= 0 and M.PROGRESS[new_status] > current_rank:
        updates['status'] = new_status
        updates['locked_at'] = None
        if message.status == M.STATUS_SENDING:
            updates['accepted_at'] = when
    M.objects.filter(pk=message.pk).update(**updates)
    if new_status in (M.STATUS_DELIVERED, M.STATUS_READ):
        WhatsAppContact.objects.filter(user_id=message.recipient_id, undeliverable_count__gt=0) \
            .update(undeliverable_count=0)
    return new_status


# ─── Mensajes que nos escriben ────────────────────────────────────────────────

def _identify(message, contacts_by_wa):
    """(contacto, mensaje nuestro al que responde). Orden: context.id, teléfono, BSUID."""
    replied = None
    context = message.get('context') if isinstance(message.get('context'), dict) else {}
    context_id = _str(context.get('id'), 128)
    if context_id:
        replied = M.objects.filter(wamid=context_id).first()
        if replied is not None:
            contact = WhatsAppContact.objects.filter(user_id=replied.recipient_id).select_related('user').first()
            if contact is not None:
                return contact, replied
    phone = from_wa_id(message.get('from'))
    if phone:
        contact = (WhatsAppContact.objects.select_related('user').filter(phone_e164=phone)
                   .order_by('-updated_at').first()
                   or WhatsAppContact.objects.select_related('user').filter(wa_id=phone.lstrip('+')).first())
        if contact is not None:
            return contact, replied
    bsuid = _str(message.get('from_user_id'), 160) or contacts_by_wa.get(_str(message.get('from'), 20), '')
    if bsuid:
        contact = WhatsAppContact.objects.select_related('user').filter(bsuid=bsuid).first()
        if contact is not None:
            return contact, replied
    return None, replied


def _classify(message):
    kind = message.get('type')
    payload = ''
    if kind == 'button' and isinstance(message.get('button'), dict):
        button = message['button']
        payload = _str(button.get('payload'), 40) or catalog.BUTTON_TEXTS.get(
            catalog.normalize_text(_str(button.get('text'), 40)), '')
    elif kind == 'interactive' and isinstance(message.get('interactive'), dict):
        reply = message['interactive'].get('button_reply')
        if isinstance(reply, dict):
            payload = _str(reply.get('id'), 40)
    elif kind == 'text' and isinstance(message.get('text'), dict):
        # El texto se usa en memoria para reconocer la baja y se descarta
        if catalog.is_optout_text(_str(message['text'].get('body'), 200)):
            return WhatsAppInbound.KIND_OPTOUT
        return WhatsAppInbound.KIND_OTHER
    elif kind in (None, 'unsupported'):
        return WhatsAppInbound.KIND_UNSUPPORTED
    else:
        return WhatsAppInbound.KIND_OTHER
    return {catalog.PAYLOAD_CONFIRM: WhatsAppInbound.KIND_CONFIRM, catalog.PAYLOAD_NOT_ME: WhatsAppInbound.KIND_NOT_ME,
            catalog.PAYLOAD_OPTOUT: WhatsAppInbound.KIND_OPTOUT}.get(payload, WhatsAppInbound.KIND_OTHER)


def apply_inbound(message, contacts_by_wa, now):
    if not isinstance(message, dict):
        return 'ignored'
    wamid = _str(message.get('id'), 128)
    if not wamid:
        return 'ignored'
    if WhatsAppInbound.objects.filter(wamid=wamid).exists():
        return 'duplicate'
    contact, replied = _identify(message, contacts_by_wa)
    if contact is None:
        return 'unknown_sender'     # no se guarda ni se responde a números que no son de una cuenta
    kind = _classify(message)
    try:
        with transaction.atomic():
            WhatsAppInbound.objects.create(wamid=wamid, contact=contact, kind=kind, in_reply_to=replied,
                                           received_at=now)
    except IntegrityError:
        return 'duplicate'
    updates = {'last_inbound_at': now}
    bsuid = _str(message.get('from_user_id'), 160)
    if bsuid and bsuid != contact.bsuid:
        updates['bsuid'] = bsuid
    WhatsAppContact.objects.filter(pk=contact.pk).update(**updates)
    contact.last_inbound_at = now
    if kind == WhatsAppInbound.KIND_CONFIRM:
        services.inbound_confirm(contact, wamid, now)
    elif kind == WhatsAppInbound.KIND_NOT_ME:
        services.inbound_not_me(contact, wamid, now)
    elif kind == WhatsAppInbound.KIND_OPTOUT:
        services.inbound_optout(contact, wamid, now)
    else:
        services.inbound_other(contact, now)
    return kind


# ─── Entrada principal ────────────────────────────────────────────────────────

def process_payload(data, now=None):
    """Aplica un webhook ya autenticado. Devuelve un resumen de conteos (sin datos personales)."""
    now = now or timezone.now()
    summary = {'statuses': 0, 'messages': 0, 'ignored_entries': 0, 'errors': 0}
    if not isinstance(data, dict) or data.get('object') != 'whatsapp_business_account':
        summary['ignored_entries'] += 1
        return summary
    waba_id = str(getattr(settings, 'WHATSAPP_CLOUD_WABA_ID', '') or '')
    phone_id = str(getattr(settings, 'WHATSAPP_CLOUD_PHONE_NUMBER_ID', '') or '')
    entries = data.get('entry') if isinstance(data.get('entry'), list) else []
    with transaction.atomic():
        for entry in entries:
            if not isinstance(entry, dict) or not waba_id or str(entry.get('id')) != waba_id:
                summary['ignored_entries'] += 1
                continue
            for change in entry.get('changes') or []:
                if not isinstance(change, dict) or change.get('field') != 'messages':
                    continue
                value = change.get('value') if isinstance(change.get('value'), dict) else {}
                metadata = value.get('metadata') if isinstance(value.get('metadata'), dict) else {}
                if not phone_id or str(metadata.get('phone_number_id')) != phone_id:
                    summary['ignored_entries'] += 1
                    continue
                for status in value.get('statuses') or []:
                    apply_status(status, now)
                    summary['statuses'] += 1
                contacts_by_wa = {}
                for item in value.get('contacts') or []:
                    if isinstance(item, dict):
                        contacts_by_wa[_str(item.get('wa_id'), 20)] = _str(item.get('user_id'), 160)
                for message in value.get('messages') or []:
                    apply_inbound(message, contacts_by_wa, now)
                    summary['messages'] += 1
                for error in value.get('errors') or []:
                    if isinstance(error, dict):
                        summary['errors'] += 1
                        logger.warning('whatsapp webhook account error code=%s', error.get('code'))
    return summary
