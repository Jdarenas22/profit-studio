"""Clonar un plan a borrador (fase D, D-03; docs/contracts/plan-ia-fase-d.md, sección 7).

El entrenador copia un plan aprobado, reemplazado o archivado a un borrador nuevo (`in_review`) para
ajustarlo y aprobarlo. Lo que la clienta ve NO cambia hasta que se apruebe la copia (la lógica de
`services.approve_plan` reemplaza al vigente). No llama a la IA, no crea trabajos y no manda correos.

Reglas (resumen):
- La copia es IDÉNTICA al contenido que la clienta ve hoy. No recalcula objetivos ni medidas: para
  eso se regenera.
- Procedencia: `health_profile`, `profile_version` y `measurement` se conservan del origen (si la
  ficha cambió, la copia sale «obsoleta» y aprobar exige confirmación); las advertencias (`flags`) se
  recalculan con la ficha de HOY. `ai_item_ids` queda vacío: la adaptación cuenta lo que el entrenador
  quita respecto a lo que propuso la IA, y la copia no debe contar dos veces una misma corrección.
- Idempotente: el plan origen se bloquea y solo puede haber una copia en borrador por origen; el doble
  clic devuelve la misma copia. Un solo borrador por tipo y clienta.
- Alimentación con bandera roja vigente (o fuera del alcance de bienestar): no se clona (la copia no
  se podría editar ni aprobar). En ejercicio no se bloquea: los choques con la ficha son advertencia.

Nada de la ficha se escribe en logs: solo ids.
"""
import copy
import logging

from django.conf import settings
from django.db import IntegrityError, transaction

from apps.accounts.permissions import INACTIVE_CLIENT_MESSAGE
from apps.health import services as health
from apps.routines.models import Routine, RoutineDay, RoutineExercise

from . import nutrition, services, texts, wellness
from .models import (
    KIND_EXERCISE, KIND_NUTRITION, MealOption, MealOptionItem, MealSlot, NutritionPlan, Plan, PlanEvent,
)
from .rules import eligibility as rules_eligibility
from .rules.progress import KIND_LABELS

logger = logging.getLogger(__name__)

CLONE_REASON_MAX = 300
CLONABLE_STATUSES = (Plan.STATUS_APPROVED, Plan.STATUS_SUPERSEDED, Plan.STATUS_ARCHIVED)
NOTE_MAX = 500


# ─── Consultas de apoyo ───────────────────────────────────────────────────────

def existing_clone(plan):
    """La copia de `plan` que sigue como borrador o en revisión (o `None`). Se reconoce por el
    evento `cloned` del plan nuevo y por `parent`: una regeneración con IA también tiene `parent`,
    pero no ese evento."""
    return (Plan.objects.filter(client_id=plan.client_id, kind=plan.kind, parent_id=plan.pk,
                                status__in=Plan.PENDING_STATUSES, events__action=PlanEvent.ACTION_CLONED)
            .order_by('-version').first())


def other_pending(plan, exclude=None):
    """Otro borrador o plan en revisión del mismo tipo y clienta (o `None`)."""
    plans = Plan.objects.filter(client_id=plan.client_id, kind=plan.kind,
                                status__in=Plan.PENDING_STATUSES).exclude(pk=plan.pk)
    if exclude is not None:
        plans = plans.exclude(pk=exclude.pk)
    return plans.order_by('-version').first()


def current_version(client_id, kind):
    """Versión del plan aprobado hoy del tipo (o `None`)."""
    return Plan.objects.filter(client_id=client_id, kind=kind, status=Plan.STATUS_APPROVED
                               ).values_list('version', flat=True).first()


def _has_content(plan):
    if plan.kind == KIND_EXERCISE:
        return plan.routine_id is not None
    return NutritionPlan.objects.filter(plan_id=plan.pk).exists()


def _kind_name(kind):
    return KIND_LABELS[kind].split(' de ', 1)[1]


def other_draft_message(plan, other):
    return texts.CLONE_MESSAGES['other_draft'].format(kind=_kind_name(plan.kind), version=other.version)


def clone_state(plan):
    """El diccionario `clone` del contexto de `trainer/plan_detail.html` (sección 7.8), o `None` si
    el plan es un borrador o está en revisión (no se muestra nada de clonar). Solo comprobaciones
    baratas; las caras (bandera roja de alimentación) las hace `clone_plan` al pulsar el botón."""
    if plan.status not in CLONABLE_STATUSES:
        return None
    client = plan.client
    existing = existing_clone(plan)
    other = other_pending(plan, exclude=existing)
    replaces = plan.version if plan.is_approved else current_version(plan.client_id, plan.kind)
    reason = ''
    if not client.is_active:
        reason = INACTIVE_CLIENT_MESSAGE
    elif existing is not None:
        reason = texts.CLONE_MESSAGES['has_copy']
    elif other is not None:
        reason = other_draft_message(plan, other)
    elif plan.is_simulated and not settings.DEBUG:
        reason = texts.CLONE_MESSAGES['simulated']
    elif not _has_content(plan):
        reason = texts.CLONE_MESSAGES['no_content']
    elif services.active_job(client, plan.kind) is not None:
        reason = texts.CLONE_MESSAGES['job_running']
    return {
        'allowed': not reason,
        'blocked_reason': reason,
        'existing_draft': existing,
        'other_draft': other,
        'replaces_version': replaces,
        'reason_max': CLONE_REASON_MAX,
    }


# ─── Copia de contenido ───────────────────────────────────────────────────────

def _fields(instance, exclude=()):
    """Valores copiables de una fila (copia profunda de los JSON). Se leen del modelo para que un
    campo nuevo (como `nutrient_snapshot`) se copie solo; `exclude` son nombres de campo."""
    skip = {'id', *exclude}
    return {field.attname: copy.deepcopy(getattr(instance, field.attname))
            for field in instance._meta.concrete_fields if field.name not in skip}


def _clone_routine(source, actor):
    """`Routine` nueva e INACTIVA con los días y ejercicios que la clienta ve hoy en la rutina del origen."""
    original = Routine.objects.get(pk=source.routine_id)
    routine = Routine.objects.create(
        name=original.name, user_id=source.client_id, trainer=actor, notes=original.notes, is_active=False)
    for day in original.days.prefetch_related('exercises'):
        new_day = RoutineDay.objects.create(routine=routine, name=day.name, order=day.order)
        RoutineExercise.objects.bulk_create([
            RoutineExercise(day=new_day, **_fields(item, exclude=('day',))) for item in day.exercises.all()
        ])
    return routine


def _clone_nutrition(source, plan):
    """Objetivos, comidas y SOLO las opciones activas (con sus alimentos). Devuelve cuántas opciones
    traían la marca «pendiente de revisión» (la copia pasa por la revisión completa del entrenador)."""
    original = NutritionPlan.objects.get(plan_id=source.pk)
    nutrition_plan = NutritionPlan.objects.create(plan=plan, **_fields(original, exclude=('plan',)))
    unreviewed = 0
    for slot in original.slots.all():
        new_slot = MealSlot.objects.create(nutrition_plan=nutrition_plan,
                                           **_fields(slot, exclude=('nutrition_plan',)))
        options = MealOption.objects.filter(slot=slot, is_active=True).order_by('position', 'pk')
        for option in options.prefetch_related('items'):
            unreviewed += bool(option.needs_review)
            new_option = MealOption.objects.create(
                slot=new_slot, replaces=None, needs_review=False, is_active=True,
                **_fields(option, exclude=('slot', 'replaces', 'needs_review', 'is_active', 'created_at')))
            MealOptionItem.objects.bulk_create([
                MealOptionItem(option=new_option, **_fields(item, exclude=('option',)))
                for item in option.items.all()
            ])
    return unreviewed


def _current_yellow_codes(client, kind):
    """Advertencias (amarillas) de la ficha de HOY para el tipo de plan; vacío sin ficha."""
    report = health.profile_flags(client, health.current_profile(client))
    return list(rules_eligibility.evaluate(report, kind).yellow_codes)


def _note(source, reason, unreviewed):
    note = f'Copia de la versión {source.version}.'
    if reason:
        note += f' Motivo: {reason}'
    if unreviewed:
        note += f' Incluye {unreviewed} opción(es) pedidas por la clienta que no estaban revisadas.'
    return note[:NOTE_MAX]


# ─── Caso de uso ──────────────────────────────────────────────────────────────

def clone_plan(plan, actor, reason=''):
    """Crea el borrador a partir de `plan`. Devuelve `(plan_nuevo, creado)`; `creado=False` si ya
    existía la copia (doble clic). Lanza `PlanNotAllowed`, `PlanClientInactive` o `PlanStateError`
    (con el texto para mostrar). Orden de comprobaciones: sección 7.5 del contrato."""
    services._check_writable(plan, actor)                      # 1. acceso y cuenta activa
    reason = (reason or '').strip()[:CLONE_REASON_MAX]
    try:
        with transaction.atomic():
            return _clone_locked(plan, actor, reason)
    except IntegrityError:
        # Otro clon simultáneo (de otro origen) se llevó el mismo número de versión
        logger.warning('plan clone conflict source_id=%s actor_id=%s', plan.pk, actor.pk)
        existing = existing_clone(plan)
        if existing is not None:
            return existing, False
        raise services.PlanStateError(texts.CLONE_MESSAGES['race']) from None


def _clone_locked(plan, actor, reason):
    source = Plan.objects.select_for_update().get(pk=plan.pk)  # 2. el doble clic espera al primero
    if source.status not in CLONABLE_STATUSES:                 # 3. estado del origen
        raise services.PlanStateError(texts.CLONE_MESSAGES['is_draft'])
    if not _has_content(source):                               # 4. contenido
        raise services.PlanStateError(texts.CLONE_MESSAGES['no_content'])
    if source.is_simulated and not settings.DEBUG:             # 5. no se podría aprobar
        raise services.PlanStateError(texts.CLONE_MESSAGES['simulated'])
    existing = existing_clone(source)                          # 6. idempotencia / un borrador por tipo
    if existing is not None:
        return existing, False
    other = other_pending(source)
    if other is not None:
        raise services.PlanStateError(other_draft_message(source, other))
    client = plan.client
    if services.active_job(client, source.kind) is not None:   # 7. un borrador ya se está generando
        raise services.PlanStateError(texts.CLONE_MESSAGES['job_running'])
    if source.kind == KIND_NUTRITION:                          # 8. alimentación: bandera roja / alcance
        _allowed, red = nutrition.current_allowed_ids(source)
        if red:
            key = 'out_of_scope' if wellness.OUT_OF_SCOPE in red else 'red_flag'
            raise services.PlanStateError(texts.CLONE_MESSAGES[key])

    routine = _clone_routine(source, actor) if source.kind == KIND_EXERCISE else None   # 9. crear
    new = Plan.objects.create(
        client=client, kind=source.kind, version=services.next_version(client, source.kind),
        status=Plan.STATUS_IN_REVIEW, parent=source, routine=routine,
        health_profile_id=source.health_profile_id, profile_version=source.profile_version,
        measurement_id=source.measurement_id, flags=_current_yellow_codes(client, source.kind),
        is_simulated=source.is_simulated, model_name=source.model_name, prompt_version=source.prompt_version,
        ruleset_version=source.ruleset_version, ai_item_ids=[], reviewed_by=actor,
    )
    unreviewed = _clone_nutrition(source, new) if source.kind == KIND_NUTRITION else 0
    PlanEvent.objects.create(plan=new, actor=actor, action=PlanEvent.ACTION_CLONED,
                             note=_note(source, reason, unreviewed))
    logger.info('plan cloned plan_id=%s source_id=%s actor_id=%s', new.pk, source.pk, actor.pk)
    return new, True


def conflicts_message(plan):
    """Aviso (o `''`) de lo que de la copia ya no es compatible con la ficha de hoy."""
    if plan.kind == KIND_EXERCISE:
        count = len(services.routine_conflicts(plan))
        key = 'conflicts_exercise'
    else:
        count = len(nutrition.option_conflicts(plan))
        key = 'conflicts_nutrition'
    return texts.CLONE_MESSAGES[key].format(count=count) if count else ''
