import hashlib
import hmac
import logging

logger = logging.getLogger(__name__)


def compute_integrity_signature(reference: str, amount_cents: int, currency: str, secret: str) -> str:
    """
    Wompi checkout widget integrity signature.
    SHA256(reference + amount_in_cents + currency + integrity_secret)
    """
    raw = f"{reference}{amount_cents}{currency}{secret}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def verify_webhook_event(payload: dict, events_secret: str) -> bool:
    """
    Verify Wompi webhook event authenticity.

    Wompi sends a `signature` block with:
      - properties: list of dot-notation keys whose values to concatenate
      - checksum: SHA256(value1 + value2 + ... + events_secret)

    https://docs.wompi.co/docs/en/events
    """
    try:
        sig_block = payload.get('signature', {})
        checksum = sig_block.get('checksum', '')
        properties = sig_block.get('properties', [])

        if not checksum or not properties:
            logger.warning("Wompi webhook: missing signature block")
            return False

        data = payload.get('data', {})
        concatenated = ''
        for prop in properties:
            # prop looks like "transaction.id", "transaction.status", etc.
            parts = prop.split('.')
            val = data
            for part in parts:
                val = val.get(part, '') if isinstance(val, dict) else ''
            concatenated += str(val)

        raw = concatenated + events_secret
        expected = hashlib.sha256(raw.encode('utf-8')).hexdigest()
        return hmac.compare_digest(expected, checksum)
    except Exception as exc:
        logger.error(f"Wompi signature verification error: {exc}")
        return False
