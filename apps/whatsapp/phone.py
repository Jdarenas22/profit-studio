"""Normalización y enmascarado de números (contrato, sección 4.4). Python puro, sin Django.

Fase 1: solo celulares colombianos (+57 3XX XXX XXXX). La salida es E.164 con `+`.
"""
import re

_SEPARATORS = re.compile(r'[\s\-.()]')
_CO_MOBILE = re.compile(r'^\+573\d{9}$')

INVALID_MESSAGE = 'Escribe tu celular colombiano, por ejemplo 300 123 4567.'


class InvalidPhone(ValueError):
    """El número no es un celular colombiano válido."""


def normalize_phone(raw):
    """Devuelve el número en E.164 (`+573001234567`) o lanza `InvalidPhone`."""
    text = _SEPARATORS.sub('', str(raw or '').strip())
    if not text:
        raise InvalidPhone(INVALID_MESSAGE)
    if text.startswith('00'):
        text = '+' + text[2:]
    if text.startswith('+'):
        digits = text[1:]
        if not digits.isdigit():
            raise InvalidPhone(INVALID_MESSAGE)
        candidate = '+' + digits
    else:
        if not text.isdigit():
            raise InvalidPhone(INVALID_MESSAGE)
        if len(text) == 10 and text.startswith('3'):
            candidate = '+57' + text
        elif len(text) == 12 and text.startswith('573'):
            candidate = '+' + text
        else:
            raise InvalidPhone(INVALID_MESSAGE)
    if not _CO_MOBILE.match(candidate):
        raise InvalidPhone(INVALID_MESSAGE)
    return candidate


def from_wa_id(value):
    """`wa_id` / `from` de Meta (solo dígitos) -> E.164 con `+`, o '' si no sirve."""
    digits = str(value or '').strip().lstrip('+')
    if not digits.isdigit() or not 8 <= len(digits) <= 15:
        return ''
    return '+' + digits


def display(phone_e164):
    """`+573001234567` -> `+57 300 123 4567` (para la pantalla de la propia usuaria)."""
    if phone_e164 and _CO_MOBILE.match(phone_e164):
        n = phone_e164[3:]
        return f'+57 {n[:3]} {n[3:6]} {n[6:]}'
    return phone_e164 or ''


def mask(phone):
    """Para logs: solo el indicativo y los 2 últimos dígitos (`+57*******67`)."""
    text = str(phone or '')
    if len(text) < 6:
        return '***'
    return text[:3] + '*' * (len(text) - 5) + text[-2:]
