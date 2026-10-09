"""Casos de uso de las mediciones corporales (lógica de negocio, sin HTTP).

Las vistas llaman aquí; este módulo es el único que decide: qué estatura se hereda, cuántas
mediciones puede registrar una clienta por día, cuándo puede borrar una y cuándo un cambio de
peso es sospechoso. La validación de formato (coma decimal, cm -> m, rangos) vive en forms.py.

Nada de lo que se maneja aquí se escribe en logs: son datos de salud.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction

from .models import BodyMeasurement, InitialAssessment

MEMBER_DAILY_LIMIT = 3                     # mediciones que una clienta puede registrar por día
SUSPICIOUS_WEIGHT_DELTA = Decimal('5')     # kg
SUSPICIOUS_WINDOW_DAYS = 14                # solo se compara contra mediciones de este margen
SERIES_MAX_POINTS = 60
LIST_MAX_ROWS = 100


def today():
    """Hoy, igual que lo calcula `auto_now_add` del campo `date` (así "mismo día" coincide)."""
    return date.today()


# ─── Estatura y última medición ───────────────────────────────────────────────

def latest_height(user):
    """Última estatura conocida (m): última medición con estatura -> valoración inicial -> None."""
    m = (BodyMeasurement.objects.filter(user=user, height__isnull=False)
         .order_by('-date', '-pk').first())
    if m is not None:
        return m.height
    a = InitialAssessment.objects.filter(user=user).order_by('-date', '-pk').first()
    return a.height if a is not None else None


@dataclass(frozen=True)
class Anthropometrics:
    weight: Decimal
    height: Decimal | None
    measured_on: date
    days_old: int
    measurement: BodyMeasurement


def latest_anthropometrics(user):
    """Última medición de peso con la estatura vigente y su antigüedad, o None si no hay.

    Peso y estatura NO se duplican en otros modelos: quien los necesite (motor de reglas,
    ficha de salud) los pide aquí.
    """
    m = BodyMeasurement.objects.filter(user=user).order_by('-date', '-pk').first()
    if m is None:
        return None
    return Anthropometrics(
        weight=m.weight,
        height=m.height if m.height is not None else latest_height(user),
        measured_on=m.date,
        days_old=(today() - m.date).days,
        measurement=m,
    )


# ─── Lectura (clienta) ────────────────────────────────────────────────────────

def can_member_delete(measurement, user):
    """La clienta solo borra lo que ella misma registró hoy."""
    return (measurement.user_id == user.pk
            and measurement.source == BodyMeasurement.SOURCE_MEMBER
            and measurement.date == today())


def member_measurements(user):
    """Mediciones propias, más reciente primero, con `can_delete` calculado."""
    rows = list(BodyMeasurement.objects.filter(user=user).order_by('-date', '-pk')[:LIST_MAX_ROWS])
    for m in rows:
        m.can_delete = can_member_delete(m, user)
    return rows


def measurement_series(user):
    """Listas para el gráfico (más antigua primero): fechas ISO, peso, cintura y cadera."""
    rows = list(BodyMeasurement.objects.filter(user=user).order_by('-date', '-pk')[:SERIES_MAX_POINTS])
    rows.reverse()

    def num(value):
        return float(value) if value is not None else None

    return {
        'dates': [m.date.isoformat() for m in rows],
        'weight': [num(m.weight) for m in rows],
        'waist_cm': [num(m.waist_cm) for m in rows],
        'hip_cm': [num(m.hip_cm) for m in rows],
    }


def member_daily_count(user):
    return BodyMeasurement.objects.filter(
        user=user, source=BodyMeasurement.SOURCE_MEMBER, date=today()
    ).count()


def member_daily_limit_reached(user):
    return member_daily_count(user) >= MEMBER_DAILY_LIMIT


# ─── Escritura ────────────────────────────────────────────────────────────────

def _is_suspicious_weight(user, weight):
    """Cambio de más de 5 kg frente a la última medición de los últimos 14 días."""
    previous = BodyMeasurement.objects.filter(
        user=user, date__gte=today() - timedelta(days=SUSPICIOUS_WINDOW_DAYS),
    ).order_by('-date', '-pk').first()
    return previous is not None and abs(weight - previous.weight) > SUSPICIOUS_WEIGHT_DELTA


@transaction.atomic
def create_member_measurement(user, data, height):
    """Guarda la medición de la clienta. `data` = cleaned_data de MemberMeasurementForm y
    `height` = la estatura efectiva (la que escribió o la heredada). Devuelve la medición;
    `needs_review` indica que el peso cambió de forma sospechosa (se guarda igual)."""
    suspicious = _is_suspicious_weight(user, data['weight'])
    return BodyMeasurement.objects.create(
        user=user,
        trainer=None,
        source=BodyMeasurement.SOURCE_MEMBER,
        weight=data['weight'],
        height=height,
        waist_cm=data.get('waist_cm'),
        hip_cm=data.get('hip_cm'),
        body_fat_pct=data.get('body_fat_pct'),
        notes=data.get('notes', ''),
        needs_review=suspicious,
    )


def create_trainer_measurement(client, trainer, data):
    return BodyMeasurement.objects.create(
        user=client,
        trainer=trainer,
        source=BodyMeasurement.SOURCE_TRAINER,
        weight=data['weight'],
        height=data['height'],            # None si no se indicó
        waist_cm=data['waist_cm'],
        hip_cm=data['hip_cm'],
        body_fat_pct=data['body_fat_pct'],
        notes=data['notes'],
    )


def update_measurement(measurement, data):
    """Corrección por el entrenador. Se conserva `source` (quién la registró originalmente)."""
    measurement.weight = data['weight']
    measurement.height = data['height']
    measurement.waist_cm = data['waist_cm']
    measurement.hip_cm = data['hip_cm']
    measurement.body_fat_pct = data['body_fat_pct']
    measurement.notes = data['notes']
    measurement.needs_review = False      # una persona ya la revisó
    # save() recalcula IMC y clasificación; si se quitó la estatura, el IMC queda como estaba
    # solo si había uno, así que se limpia aquí.
    if measurement.height is None:
        measurement.imc = None
        measurement.imc_classification = ''
    measurement.save()
    return measurement
