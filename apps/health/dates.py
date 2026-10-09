"""Fechas de la ficha de salud: un solo lugar para "hoy" y para calcular la edad.

`today()` usa la fecha local de Bogotá (zona del proyecto) y existe aparte para poder
simular el día en las pruebas (bordes de 17/18 años, 12 meses de una autorización, etc.).
"""
from django.utils import timezone


def today():
    return timezone.localdate()


def age_on(birth_date, on):
    """Años cumplidos de `birth_date` en la fecha `on`."""
    return on.year - birth_date.year - ((on.month, on.day) < (birth_date.month, birth_date.day))


def age_today(birth_date):
    return age_on(birth_date, today())
