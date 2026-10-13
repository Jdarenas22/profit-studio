"""Adaptación a la clienta: lee su historial en la base de datos y lo convierte en un
`AdaptationProfile` (diseño: docs/contracts/plan-ia-adaptacion.md, paso 2).

Esta capa solo CONSULTA y entrega datos simples a `rules/adaptation.py` (Python puro, donde viven los
cálculos y umbrales). No guarda perfiles propios: se calcula al vuelo en cada trabajo, así borrar los
registros de la clienta basta para que desaparezca.

Reglas:
- Apagada por defecto (`AI_PLANS_ADAPTATION`). Sin consentimiento vigente de salud y de IA no se usa.
- Es opcional: si algo falla, el plan se genera igual que sin adaptación (`for_job` nunca rompe un trabajo).
- Las exclusiones solo afectan lo que se le ofrece a la IA (payload y esquema). La validación posterior
  sigue usando el catálogo completo y seguro, y el historial jamás agrega un id al catálogo permitido.
- Solo viajan códigos, ids de nuestros catálogos y números: nunca texto libre de la clienta.
"""
import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db.models import Min
from django.utils import timezone

from apps.assessments.models import BodyMeasurement
from apps.health import dates as health_dates
from apps.health import services as health
from apps.health.models import PURPOSE_AI, PURPOSE_HEALTH_DATA
from apps.routines.models import RoutineDayLog, RoutineExercise

from .models import KIND_EXERCISE, KIND_NUTRITION, MealChoice, MealOption, MealOptionItem, Plan
from .rules import adaptation as rules

logger = logging.getLogger(__name__)

PREFERENCE_DAYS = 90        # cuánto atrás se miran las comidas elegidas y las opciones descartadas
TRAINER_PLANS_MAX = 12      # planes anteriores que se comparan con el borrador de la IA
REVIEWED_STATUSES = (Plan.STATUS_APPROVED, Plan.STATUS_SUPERSEDED)


def enabled():
    return bool(getattr(settings, 'AI_PLANS_ADAPTATION', False))


@dataclass(frozen=True)
class JobAdaptation:
    """Lo que un trabajo usa de la adaptación. `exercises` / `foods` son los catálogos ya recortados
    (o `None` si no aplica a este tipo de trabajo); `history` es el bloque para el payload (o `None`)."""
    profile: rules.AdaptationProfile
    history: dict | None = None
    exercises: tuple | None = None
    foods: tuple | None = None
    excluded_exercise_ids: tuple = ()
    excluded_food_ids: tuple = ()

    @property
    def exercise_ids(self):
        return frozenset(item.id for item in self.exercises) if self.exercises is not None else None

    @property
    def food_ids(self):
        return frozenset(item.id for item in self.foods) if self.foods is not None else None

    def summary(self):
        """Códigos y conteos para `PlanGenerationJob.adaptation_summary` (sin datos personales)."""
        data = self.profile.summary()
        data['excluded_exercises'] = len(self.excluded_exercise_ids)
        data['excluded_foods'] = len(self.excluded_food_ids)
        data['history_sent'] = self.history is not None
        return data


# ─── Lectura de datos ─────────────────────────────────────────────────────────

def _local_date(moment):
    return timezone.localtime(moment).date()


def _history_since(client):
    first = Plan.objects.filter(client=client, status__in=REVIEWED_STATUSES, approved_at__isnull=False
                                ).aggregate(first=Min('approved_at'))['first']
    return _local_date(first) if first else None


def _training_days(client):
    plan = Plan.objects.filter(client=client, kind=KIND_EXERCISE, status=Plan.STATUS_APPROVED,
                               routine__isnull=False).select_related('routine').first()
    if plan is None:
        return ()
    return tuple(rules.TrainingDay(id=day.id, name=day.name, order=day.order)
                 for day in plan.routine.days.order_by('order', 'pk'))


def _training_logs(client, today):
    since = today - timedelta(days=rules.WINDOW_DAYS + 1)
    return tuple(rules.TrainingLog(day_id=day_id, on=on) for day_id, on in
                 RoutineDayLog.objects.filter(user=client, completed_at__gte=since
                                              ).values_list('routine_day_id', 'completed_at'))


def _group(rows):
    """[(clave, id)] -> {clave: {ids}}"""
    grouped = {}
    for key, value in rows:
        grouped.setdefault(key, set()).add(value)
    return grouped


def _completed_meals(client, today):
    rows = MealChoice.objects.filter(
        client=client, completed_at__isnull=False, date__gte=today - timedelta(days=PREFERENCE_DAYS),
    ).values_list('pk', 'option__items__food_id')
    return tuple(frozenset(i for i in ids if i is not None) for ids in _group(rows).values())


def _discarded_options(client):
    """Opciones que la clienta pidió reemplazar y cuyo reemplazo sigue activo (si el entrenador
    revirtió el cambio, no cuenta como descarte)."""
    since = timezone.now() - timedelta(days=PREFERENCE_DAYS)
    rows = MealOption.objects.filter(
        slot__nutrition_plan__plan__client=client, is_active=False, created_at__gte=since,
        replaced_by__is_active=True, replaced_by__origin=MealOption.ORIGIN_CLIENT_REGEN,
    ).values_list('pk', 'items__food_id')
    return tuple(frozenset(i for i in ids if i is not None) for ids in _group(rows).values())


def _trainer_removals(client):
    """Por cada plan revisado: ids que la IA propuso y ya no están en el plan final. En alimentación
    cuentan las opciones activas y las que la clienta (no el entrenador) reemplazó, para no confundir
    un descarte de la clienta con una corrección del entrenador. Planes sin `ai_item_ids` se ignoran."""
    plans = list(Plan.objects.filter(client=client, status__in=REVIEWED_STATUSES
                                     ).exclude(ai_item_ids=[]).order_by('-approved_at')[:TRAINER_PLANS_MAX])
    exercise_plans = {p.pk: p for p in plans if p.kind == KIND_EXERCISE and p.routine_id}
    nutrition_plans = {p.pk: p for p in plans if p.kind == KIND_NUTRITION}

    current_exercises = _group(
        (routine_id, exercise_id) for routine_id, exercise_id in RoutineExercise.objects.filter(
            day__routine_id__in=[p.routine_id for p in exercise_plans.values()]
        ).values_list('day__routine_id', 'exercise_id'))
    current_foods = _group(
        (plan_id, food_id) for plan_id, food_id in MealOptionItem.objects.filter(
            option__slot__nutrition_plan__plan_id__in=list(nutrition_plans), option__is_active=True
        ).values_list('option__slot__nutrition_plan__plan_id', 'food_id'))
    replaced_by_client = _group(
        (plan_id, food_id) for plan_id, food_id in MealOptionItem.objects.filter(
            option__slot__nutrition_plan__plan_id__in=list(nutrition_plans), option__is_active=False,
            option__replaced_by__origin=MealOption.ORIGIN_CLIENT_REGEN,
        ).values_list('option__slot__nutrition_plan__plan_id', 'food_id'))

    removals = []
    for plan in plans:
        proposed = set(plan.ai_item_ids)
        if plan.kind == KIND_EXERCISE and plan.pk in exercise_plans:
            removed = proposed - current_exercises.get(plan.routine_id, set())
            removals.append(rules.TrainerRemoval(exercise_ids=frozenset(removed)))
        elif plan.kind == KIND_NUTRITION:
            kept = current_foods.get(plan.pk, set()) | replaced_by_client.get(plan.pk, set())
            removals.append(rules.TrainerRemoval(food_ids=frozenset(proposed - kept)))
    return tuple(removals)


def _weights(client, today):
    since = today - timedelta(days=rules.PROGRESS_WINDOW_DAYS)
    return tuple(rules.WeightPoint(on=on, kg=float(kg)) for on, kg in BodyMeasurement.objects.filter(
        user=client, needs_review=False, date__gte=since).values_list('date', 'weight'))


def collect(client, profile, today=None):
    """Datos de la clienta como `AdaptationInput` (una consulta por señal)."""
    today = today or health_dates.today()
    return rules.AdaptationInput(
        today=today, history_since=_history_since(client), goal=client.training_goal or '',
        days_per_week=profile.training_days_per_week, training_days=_training_days(client),
        training_logs=_training_logs(client, today), completed_meals=_completed_meals(client, today),
        discarded_options=_discarded_options(client), trainer_removals=_trainer_removals(client),
        weights=_weights(client, today),
    )


# ─── Uso desde los trabajos ───────────────────────────────────────────────────

def _consented(client):
    return health.has_valid_consent(client, PURPOSE_HEALTH_DATA) and health.has_valid_consent(client, PURPOSE_AI)


def for_job(job, assessment):
    """Adaptación del trabajo, o `None` si está apagada, no hay consentimiento vigente o algo falla.
    Nunca lanza: la adaptación es opcional y un error no debe impedir generar el plan."""
    if not enabled():
        return None
    try:
        if not _consented(job.client) or assessment.profile is None:
            return None
        profile = rules.build_profile(collect(job.client, assessment.profile))
        exercises = foods = None
        excluded_exercises = excluded_foods = ()
        if job.kind == KIND_EXERCISE and assessment.catalog is not None:
            exercises, excluded_exercises = rules.exclude_exercises(
                assessment.catalog.allowed, profile.removed_exercise_ids)
        elif job.kind != KIND_EXERCISE and assessment.foods is not None:
            foods, excluded_foods = rules.exclude_foods(
                assessment.foods.allowed, set(profile.avoid_food_ids) | set(profile.removed_food_ids))
        return JobAdaptation(profile=profile, history=profile.as_payload(), exercises=exercises, foods=foods,
                             excluded_exercise_ids=excluded_exercises, excluded_food_ids=excluded_foods)
    except Exception as exc:   # noqa: BLE001 - opcional: se registra solo el tipo, nunca datos
        logger.warning('adaptation skipped job_id=%s error=%s', job.pk, type(exc).__name__)
        return None


# ─── Panel "Qué se tuvo en cuenta" (pantalla del plan del entrenador) ─────────

PANEL_FOOTNOTE = ('Es una guía suave: las reglas de seguridad no cambian y tú decides qué se aprueba.')
_ADHERENCE_TEXT = {
    'low': ('Constancia baja: completó menos de la mitad de los días de entrenamiento del último mes.', 'warning'),
    'medium': ('Constancia media: completó entre la mitad y cuatro de cada cinco días de entrenamiento.', 'info'),
    'high': ('Constancia alta: completó al menos cuatro de cada cinco días de entrenamiento.', 'good'),
}
_PROGRESS_TEXT = {
    'on_track': ('Su peso va en la dirección de su meta.', 'good'),
    'stalled': ('Su peso lleva semanas sin cambios.', 'warning'),
    'off_track': ('Su peso se mueve en dirección contraria a su meta o a un ritmo demasiado rápido.', 'warning'),
}


def _plural(count, one, many):
    return f'{count} {one if count == 1 else many}'


def panel_for_plan(plan):
    """Frases listas para mostrar al entrenador sobre qué se tuvo en cuenta al generar este plan, o
    `None` si el trabajo que lo creó no usó la adaptación (apagada o plan antiguo). Solo contiene
    textos fijos y conteos: sin nombres ni datos de la clienta."""
    job = plan.jobs.filter(kind__in=(KIND_EXERCISE, KIND_NUTRITION)).order_by('-created_at').first()
    summary = job.adaptation_summary if job is not None else None
    if not summary:
        return None
    panel = {'ready': bool(summary.get('ready')), 'weeks_of_data': summary.get('weeks_of_data', 0),
             'items': [], 'calorie_review': False, 'footnote': PANEL_FOOTNOTE}
    if not panel['ready']:
        return panel
    items = panel['items']
    if summary.get('adherence') in _ADHERENCE_TEXT:
        text, tone = _ADHERENCE_TEXT[summary['adherence']]
        items.append({'text': text, 'tone': tone})
    if summary.get('skipped_days'):
        items.append({'text': _plural(summary['skipped_days'], 'día de su rutina casi nunca se hace',
                                      'días de su rutina casi nunca se hacen') + ' (menos del 40 %).',
                      'tone': 'warning'})
    if summary.get('liked_foods'):
        items.append({'text': _plural(summary['liked_foods'], 'alimento que elige con frecuencia',
                                      'alimentos que elige con frecuencia') + ' se favorecieron.', 'tone': 'info'})
    if summary.get('avoided_foods'):
        items.append({'text': _plural(summary['avoided_foods'], 'alimento que descarta con frecuencia',
                                      'alimentos que descarta con frecuencia') + ' se evitaron.', 'tone': 'info'})
    if summary.get('removed_exercises') or summary.get('removed_foods'):
        items.append({'text': 'Se tuvieron en cuenta correcciones repetidas de revisiones anteriores '
                              '(ejercicios o alimentos que se quitaron en 2 o más planes).', 'tone': 'info'})
    if summary.get('excluded_exercises'):
        items.append({'text': _plural(summary['excluded_exercises'], 'ejercicio dejó de ofrecerse',
                                      'ejercicios dejaron de ofrecerse') + ' a la IA por esas correcciones.',
                      'tone': 'info'})
    if summary.get('excluded_foods'):
        items.append({'text': _plural(summary['excluded_foods'], 'alimento dejó de ofrecerse',
                                      'alimentos dejaron de ofrecerse') + ' a la IA.', 'tone': 'info'})
    if summary.get('progress') in _PROGRESS_TEXT:
        text, tone = _PROGRESS_TEXT[summary['progress']]
        items.append({'text': text, 'tone': tone})
    panel['calorie_review'] = bool(summary.get('suggest_calorie_review')) and plan.kind == KIND_NUTRITION
    return panel
