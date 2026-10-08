import hashlib
import hmac
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


class WompiConfigError(Exception):
    """Falta un secreto/llave de Wompi: no se debe firmar ni aceptar nada."""


def compute_integrity_signature(reference: str, amount_cents: int, currency: str, secret: str) -> str:
    """
    Firma de integridad del widget/checkout de Wompi:
    SHA256(<Referencia><Monto en centavos><Moneda><Secreto de integridad>)

    Fuente: https://docs.wompi.co/docs/colombia/widget-checkout-web/
    Si el secreto está vacío NO se calcula (una firma con secreto vacío sería
    inválida para Wompi y, peor, predecible): se lanza WompiConfigError.
    """
    if not secret:
        raise WompiConfigError('WOMPI_INTEGRITY_SECRET no está configurado')
    raw = f"{reference}{amount_cents}{currency}{secret}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _stringify(value) -> str:
    """Representación de un valor del evento tal como la concatena Wompi (JSON → texto)."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


def _resolve_property(data: dict, dotted: str):
    """Resuelve 'transaction.id' dentro de data. Lanza KeyError si no existe o es null."""
    value = data
    for part in dotted.split('.'):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(dotted)
        value = value[part]
    if value is None or isinstance(value, (dict, list)):
        raise KeyError(dotted)
    return value


def _same_checksum(expected: str, received: str) -> bool:
    """Comparación en tiempo constante, sin distinguir mayúsculas (Wompi envía HEX en mayúsculas)."""
    return hmac.compare_digest(
        expected.upper().encode('utf-8'),
        str(received).strip().upper().encode('utf-8'),
    )


def verify_webhook_event(payload: dict, events_secret: str, header_checksum: str = None) -> bool:
    """
    Verifica la autenticidad de un evento de Wompi.

    Algoritmo oficial (https://docs.wompi.co/docs/colombia/eventos/):
      1. Concatenar los valores de los campos listados en `signature.properties`
         (rutas con puntos dentro de `data`, en el orden en que llegan).
      2. Concatenar el campo `timestamp` del evento (raíz del JSON, UNIX).
      3. Concatenar el secreto de eventos del panel de Wompi.
      4. SHA256 del texto resultante.
      5. Comparar con `signature.checksum` (que también llega en el header
         `X-Event-Checksum`).

    Reglas de seguridad adicionales:
      - Sin secreto de eventos se rechaza SIEMPRE (nunca se acepta una firma
        calculada con secreto vacío).
      - Sin `timestamp`, sin `properties` o con una propiedad inexistente se rechaza.
      - Si llega el header `X-Event-Checksum`, debe coincidir con el calculado.
      - Comparación en tiempo constante.
    """
    if not events_secret:
        logger.error("Wompi webhook: WOMPI_EVENTS_SECRET no configurado — evento rechazado")
        return False

    try:
        if not isinstance(payload, dict):
            return False
        sig_block = payload.get('signature')
        if not isinstance(sig_block, dict):
            logger.warning("Wompi webhook: missing signature block")
            return False

        checksum = sig_block.get('checksum')
        properties = sig_block.get('properties')
        timestamp = payload.get('timestamp')
        data = payload.get('data')

        if not checksum or not isinstance(checksum, str):
            logger.warning("Wompi webhook: missing checksum")
            return False
        if not properties or not isinstance(properties, list) or not all(isinstance(p, str) for p in properties):
            logger.warning("Wompi webhook: missing signature properties")
            return False
        if timestamp is None or isinstance(timestamp, (bool, dict, list)) or timestamp == '':
            logger.warning("Wompi webhook: missing timestamp")
            return False
        if not isinstance(data, dict):
            logger.warning("Wompi webhook: missing data")
            return False

        try:
            concatenated = ''.join(_stringify(_resolve_property(data, p)) for p in properties)
        except KeyError as exc:
            logger.warning(f"Wompi webhook: signature property not found in event: {exc}")
            return False

        raw = f"{concatenated}{_stringify(timestamp)}{events_secret}"
        expected = hashlib.sha256(raw.encode('utf-8')).hexdigest()

        if not _same_checksum(expected, checksum):
            return False
        if header_checksum and not _same_checksum(expected, header_checksum):
            logger.warning("Wompi webhook: X-Event-Checksum header does not match")
            return False
        return True
    except Exception as exc:  # defensivo: ante cualquier rareza, rechazar
        logger.error(f"Wompi signature verification error: {exc}")
        return False


# ─── Aplicación de eventos de transacción (lógica de negocio) ──────────────────

def activate_membership(payment):
    """
    Activa o renueva la membresía del usuario por un pago aprobado.

    NO captura excepciones: debe ejecutarse dentro de la transacción del webhook,
    de modo que si falla se revierte todo (el pago no queda "aprobado" sin
    membresía) y Wompi reintenta el evento.

    Fechas: si no hay membresía, o está vencida/desactivada, se activa desde hoy
    (start_date = hoy, end_date = hoy + duración). Si sigue vigente, se extiende
    desde su fecha de fin actual (no se pierden días ya pagados).
    """
    from django.db import transaction
    from apps.memberships.models import Membership

    plan = payment.plan
    membership, created = Membership.objects.get_or_create(
        user=payment.user,
        defaults={
            'plan': plan,
            'start_date': timezone.localdate(),
            'end_date': timezone.localdate(),
            'is_active': False,
            'activated_by': None,
        },
    )
    # Bloquea la fila para que dos pagos distintos del mismo usuario no se pisen
    membership = Membership.objects.select_for_update().get(pk=membership.pk)
    if created or not membership.is_valid:
        membership.activate(plan, plan.duration_days, activated_by=None)
        action = 'activated'
    else:
        membership.plan = plan
        membership.renew(plan.duration_days, activated_by=None)
        action = 'renewed'
    logger.info(f"Membership {action} for user {payment.user_id} (payment {payment.reference})")
    return membership


def apply_transaction_event(payload: dict) -> str:
    """
    Aplica un evento `transaction.updated` (ya autenticado) al Payment local.

    Devuelve un texto con el resultado (útil para logs y pruebas):
      'approved' | 'declined' | 'voided' | 'error_status' | 'pending'
      'duplicate'          el pago ya estaba aprobado (idempotencia)
      'unknown_reference'  no existe un Payment con esa referencia
      'invalid'            el evento no trae transacción/referencia utilizable
      'mismatch'           monto o moneda no coinciden con el Payment
      'transaction_reused' ese id de transacción de Wompi ya pertenece a otro Payment
      'cannot_activate'    pago aprobado pero sin usuario o plan (revisión manual)

    Si la activación de la membresía lanza una excepción, ésta SE PROPAGA y la
    transacción se revierte: el llamador debe responder 5xx para que Wompi reintente.
    """
    from django.db import transaction
    from .models import Payment

    tx = (payload.get('data') or {}).get('transaction')
    if not isinstance(tx, dict):
        logger.warning("Wompi webhook: event without transaction data")
        return 'invalid'

    reference = tx.get('reference')
    if not isinstance(reference, str) or not reference:
        logger.warning("Wompi webhook: event without reference")
        return 'invalid'
    wompi_status = tx.get('status') if isinstance(tx.get('status'), str) else ''
    wompi_id = tx.get('id') if isinstance(tx.get('id'), str) else ''
    method_type = tx.get('payment_method_type') if isinstance(tx.get('payment_method_type'), str) else ''
    tx_amount = tx.get('amount_in_cents')
    tx_currency = tx.get('currency')

    with transaction.atomic():
        # Bloqueo de fila: dos entregas simultáneas del mismo evento se serializan
        # (en PostgreSQL; SQLite ignora el bloqueo pero serializa las escrituras).
        try:
            payment = Payment.objects.select_for_update().get(reference=reference)
        except Payment.DoesNotExist:
            logger.warning(f"Wompi webhook: unknown reference {reference!r}")
            return 'unknown_reference'

        # Idempotencia: un pago aprobado es terminal
        if payment.status == Payment.STATUS_APPROVED:
            if wompi_status == 'VOIDED':
                logger.warning(
                    f"Wompi webhook: VOIDED recibido para pago ya aprobado {reference} — "
                    "revisar manualmente (no se revoca la membresía automáticamente)"
                )
            return 'duplicate'

        # El monto y la moneda del evento deben coincidir con lo que cobramos
        amount_ok = isinstance(tx_amount, int) and not isinstance(tx_amount, bool) \
            and tx_amount == payment.amount_cents
        if not amount_ok or tx_currency != payment.currency:
            logger.warning(
                f"Wompi webhook: monto/moneda NO coinciden para {reference}: "
                f"evento={tx_amount!r} {tx_currency!r} vs pago={payment.amount_cents} {payment.currency} "
                f"(estado Wompi={wompi_status}) — no se activa; revisar manualmente"
            )
            if wompi_status == 'APPROVED':
                # Hubo cobro con datos inconsistentes: dejar rastro visible en el admin
                payment.status = Payment.STATUS_ERROR
                payment.wompi_transaction_id = wompi_id
                payment.payment_method_type = method_type
                payment.raw_webhook = payload
                payment.save()
            return 'mismatch'

        # Un id de transacción de Wompi solo puede pertenecer a un Payment
        # (evita reutilizar un evento firmado cambiando la referencia, que no va firmada)
        if wompi_id and Payment.objects.filter(wompi_transaction_id=wompi_id).exclude(pk=payment.pk).exists():
            logger.warning(f"Wompi webhook: transaction {wompi_id} already linked to another payment; "
                           f"ignored for {reference}")
            return 'transaction_reused'

        payment.wompi_transaction_id = wompi_id
        payment.payment_method_type = method_type
        payment.raw_webhook = payload

        if wompi_status == 'APPROVED':
            if not payment.user_id or not payment.plan_id:
                logger.error(f"Wompi webhook: pago aprobado {reference} sin usuario o plan — revisión manual")
                payment.status = Payment.STATUS_ERROR
                payment.save()
                return 'cannot_activate'
            payment.status = Payment.STATUS_APPROVED
            payment.save()
            activate_membership(payment)   # si falla, se revierte todo
            logger.info(f"Payment APPROVED: {reference} | user={payment.user_id} | method={method_type}")
            return 'approved'
        if wompi_status == 'DECLINED':
            payment.status = Payment.STATUS_DECLINED
            payment.save()
            logger.info(f"Payment DECLINED: {reference}")
            return 'declined'
        if wompi_status == 'VOIDED':
            payment.status = Payment.STATUS_VOIDED
            payment.save()
            return 'voided'
        if wompi_status == 'ERROR':
            payment.status = Payment.STATUS_ERROR
            payment.save()
            return 'error_status'
        payment.save()
        return 'pending'
