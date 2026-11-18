"""Ventana horaria de envío (Ley 2300 de 2023, art. 3) y festivos de Colombia. Python puro.

Propuesta conservadora aprobada (contrato, sección 7.5) **[A VALIDAR por abogado]**: lunes a
viernes de 7:00 a 19:00 y sábados de 8:00 a 15:00, hora de Bogotá; nunca domingos ni festivos.

Festivos (Ley 51 de 1983, "Ley Emiliani"): fechas fijas, fechas que se trasladan al lunes siguiente
y fechas que dependen de la Pascua (algoritmo de Gauss/Meeus para el domingo de Pascua). Sin
dependencias externas.
"""
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

BOGOTA = ZoneInfo('America/Bogota')

# (día de la semana: 0 = lunes) -> (apertura, cierre). Domingo no aparece: cerrado.
WINDOWS = {
    0: (time(7, 0), time(19, 0)),
    1: (time(7, 0), time(19, 0)),
    2: (time(7, 0), time(19, 0)),
    3: (time(7, 0), time(19, 0)),
    4: (time(7, 0), time(19, 0)),
    5: (time(8, 0), time(15, 0)),
}

_FIXED = ((1, 1), (5, 1), (7, 20), (8, 7), (12, 8), (12, 25))
_MOVED_TO_MONDAY = ((1, 6), (3, 19), (6, 29), (8, 15), (10, 12), (11, 1), (11, 11))


def easter_sunday(year):
    """Domingo de Pascua (calendario gregoriano, algoritmo anónimo de Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def _next_monday(day):
    return day + timedelta(days=(7 - day.weekday()) % 7)


@lru_cache(maxsize=32)
def colombian_holidays(year):
    """Conjunto de festivos de Colombia del año."""
    days = {date(year, month, day) for month, day in _FIXED}
    days |= {_next_monday(date(year, month, day)) for month, day in _MOVED_TO_MONDAY}
    easter = easter_sunday(year)
    days.add(easter - timedelta(days=3))                      # Jueves Santo
    days.add(easter - timedelta(days=2))                      # Viernes Santo
    days.add(_next_monday(easter + timedelta(days=39)))       # Ascensión del Señor
    days.add(_next_monday(easter + timedelta(days=60)))       # Corpus Christi
    days.add(_next_monday(easter + timedelta(days=68)))       # Sagrado Corazón
    return frozenset(days)


def is_holiday(day):
    return day in colombian_holidays(day.year)


def _local(moment):
    if moment.tzinfo is None:
        raise ValueError('Se necesita una fecha con zona horaria.')
    return moment.astimezone(BOGOTA)


def in_window(moment):
    """¿Se puede enviar un aviso iniciado por el negocio en este momento?"""
    local = _local(moment)
    window = WINDOWS.get(local.weekday())
    if window is None or is_holiday(local.date()):
        return False
    start, end = window
    return start <= local.time() < end


def next_open(moment):
    """El primer instante >= `moment` dentro de la ventana (con zona de Bogotá)."""
    local = _local(moment)
    if in_window(local):
        return local
    day = local.date()
    for offset in range(0, 15):
        current = day + timedelta(days=offset)
        window = WINDOWS.get(current.weekday())
        if window is None or is_holiday(current):
            continue
        start, end = window
        opening = datetime.combine(current, start, tzinfo=BOGOTA)
        if offset == 0:
            if local.time() < start:
                return opening
            continue   # hoy ya cerró
        return opening
    raise RuntimeError('No hay ventana de envío en los próximos 15 días.')   # no debería pasar
