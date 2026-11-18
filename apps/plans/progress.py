"""Progreso de la clienta: lectura de la base de datos para la pantalla D-02 (docs/contracts/
plan-ia-fase-d.md, sección 6). Los cálculos viven en `rules/progress.py` (Python puro).

Esta capa solo CONSULTA y entrega el contexto de la plantilla. Un número FIJO de consultas (sin una
por medida ni por plan). Sin consentimiento `health_data` vigente (`content_visible` falso) no se
leen medidas, comidas ni la ficha: solo los días de rutina marcados y los planes aprobados.

Nada de esto se escribe en logs.
"""
from datetime import timedelta

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Min, Q
from django.utils import timezone

from apps.assessments.models import BodyMeasurement
from apps.health import dates as health_dates
from apps.health import services as health
from apps.routines.models import RoutineDayLog

from . import adaptation as adaptation_service
from .models import KIND_EXERCISE, KIND_NUTRITION, MealChoice, Plan
from .rules import progress as rules

MEASUREMENTS_MAX = 60          # mismo tope que `assessments.services.SERIES_MAX_POINTS`
SOURCE_LABELS = {BodyMeasurement.SOURCE_MEMBER: 'Clienta', BodyMeasurement.SOURCE_TRAINER: 'Entrenador/a'}
CHARTS = (
    ('weight', 'Peso', 'kg', 'weight', 1),
    ('waist_cm', 'Cintura', 'cm', 'waist_cm', 1),
    ('hip_cm', 'Cadera', 'cm', 'hip_cm', 1),
    ('imc', 'IMC', '', 'imc', 1),
)


def _local_date(moment):
    return timezone.localtime(moment).date() if moment is not None else None


def _point(m):
    return rules.MeasurementPoint(
        pk=m.pk, date=m.date, weight=m.weight, waist_cm=m.waist_cm, hip_cm=m.hip_cm,
        body_fat_pct=m.body_fat_pct, imc=m.imc, needs_review=m.needs_review)


def _approved_plans(client, with_measurement):
    """Planes aprobados hoy (a lo sumo uno por tipo), con los objetivos cargados. La medida base solo se
    lee con consentimiento (`with_measurement`): sin él ni siquiera se consulta."""
    related = ('measurement', 'nutrition') if with_measurement else ('nutrition',)
    return list(Plan.objects.filter(client=client, status=Plan.STATUS_APPROVED).select_related(*related))


def _first_approvals(client):
    """Fecha local del primer plan aprobado: de cualquier tipo (la ventana de la adaptación), de
    ejercicio y de alimentación. Misma definición que `adaptation.history_since`: planes `approved`
    o `superseded`. Una sola consulta."""
    row = Plan.objects.filter(
        client=client, status__in=adaptation_service.REVIEWED_STATUSES, approved_at__isnull=False,
    ).aggregate(
        anything=Min('approved_at'),
        exercise=Min('approved_at', filter=Q(kind=KIND_EXERCISE)),
        nutrition=Min('approved_at', filter=Q(kind=KIND_NUTRITION)),
    )
    return {name: _local_date(value) for name, value in row.items()}


def _plan_info(plan, today):
    if plan is None:
        return None
    valid_until = plan.valid_until
    return {'version': plan.version, 'approved_on': _local_date(plan.approved_at), 'valid_until': valid_until,
            'expired': bool(valid_until and valid_until < today)}


def _nutrition_of(plan):
    try:
        return plan.nutrition
    except ObjectDoesNotExist:
        return None


def build_context(client, *, content_visible, today=None):
    """Claves del contexto de `trainer/client_progress.html` (sin `client` ni `client_active`)."""
    today = today or health_dates.today()
    approved = {plan.kind: plan for plan in _approved_plans(client, content_visible)}
    first = _first_approvals(client)

    # Medidas (solo con consentimiento): las últimas 60 y el total
    rows = []
    total = 0
    if content_visible:
        rows = list(BodyMeasurement.objects.filter(user=client).order_by('-date', '-pk')[:MEASUREMENTS_MAX + 1])
        total = BodyMeasurement.objects.filter(user=client).count()
    truncated = len(rows) > MEASUREMENTS_MAX
    rows = rows[:MEASUREMENTS_MAX]
    points = [_point(m) for m in rows]

    baseline = None
    baseline_pk = None
    if content_visible:
        candidates = [(kind, plan.version, _point(plan.measurement))
                      for kind, plan in approved.items() if plan.measurement_id is not None]
        chosen = rules.pick_baseline(candidates)
        baseline = rules.baseline_info(chosen, points)
        baseline_pk = chosen[2].pk if chosen is not None else None
    ascending = list(reversed(rows))
    charts = []
    if content_visible:
        charts = [
            rules.build_chart(
                key=key, label=label, unit=unit, decimals=decimals, baseline_pk=baseline_pk,
                rows=[(m.pk, m.date, getattr(m, field), m.needs_review) for m in ascending])
            for key, label, unit, field, decimals in CHARTS
        ]
    table = [{
        'pk': m.pk, 'date': m.date, 'weight': m.weight, 'waist_cm': m.waist_cm, 'hip_cm': m.hip_cm,
        'body_fat_pct': m.body_fat_pct, 'imc': m.imc, 'imc_classification': m.imc_classification,
        'source': m.source, 'source_label': SOURCE_LABELS.get(m.source, ''), 'needs_review': m.needs_review,
        'is_baseline': m.pk == baseline_pk,
    } for m in rows]

    due, due_text = (False, '')
    last = None
    if content_visible:
        due, due_text = rules.measure_due(points, today)
        if points:
            newest = max(points, key=lambda p: p.order)
            last = {'date': newest.date, 'days_old': (today - newest.date).days}

    # Constancia: días de rutina (siempre) y comidas + meta de la ficha (con consentimiento)
    since = today - timedelta(days=rules.WINDOW_DAYS)
    training_days = list(RoutineDayLog.objects.filter(
        user=client, completed_at__gt=since, completed_at__lte=today).values_list('completed_at', flat=True))
    meal_days = None
    days_per_week = None
    if content_visible:
        meal_days = list(MealChoice.objects.filter(
            client=client, completed_at__isnull=False, option__is_active=True, date__gt=since, date__lte=today,
        ).values_list('date', flat=True))
        profile = health.current_profile(client)
        days_per_week = profile.training_days_per_week if profile is not None else None
    nutrition_plan = approved.get(KIND_NUTRITION)
    nutrition = _nutrition_of(nutrition_plan) if nutrition_plan is not None else None
    adherence = rules.adherence(
        today=today, training_days=training_days, meal_days=meal_days,
        training_since=first['anything'], meals_since=first['nutrition'],
        exercise_plan_ever=first['exercise'] is not None, days_per_week=days_per_week,
        meals_per_day=nutrition.meals_per_day if nutrition is not None else None,
        has_current_nutrition=nutrition is not None, meals_visible=content_visible)

    return {
        'today': today,
        'current_plans': {KIND_EXERCISE: _plan_info(approved.get(KIND_EXERCISE), today),
                          KIND_NUTRITION: _plan_info(nutrition_plan, today)},
        'measurements': table,
        'measurements_count': total,
        'measurements_truncated': truncated,
        'charts': charts,
        'baseline': baseline,
        'last_measurement': last,
        'measure_due': due,
        'measure_due_text': due_text,
        'adherence': adherence,
    }

