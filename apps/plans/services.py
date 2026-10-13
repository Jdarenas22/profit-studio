"""Casos de uso de los planes con IA (lógica de negocio, sin HTTP). Fase B: plan de ejercicio; la
fase C (alimentación) vive en `nutrition.py` y reutiliza `assess`, el cupo y la aprobación de aquí.

Reglas (docs/contracts/plan-ia.md, secciones 3, 5, 6 y 8):
- La IA nunca decide si se puede generar: `assess` combina la ficha, las banderas, los
  consentimientos, los textos legales, el cupo de uso y el catálogo filtrado.
- Con bandera roja (o sin ejercicios compatibles) no se llama a la IA: el trabajo queda `blocked`
  con los CÓDIGOS y se avisa al entrenador (sin datos de salud en el correo).
- El resultado es un borrador (`draft_ai`) que la clienta NO ve hasta que el entrenador lo
  aprueba. El ejercicio es una `Routine` real inactiva (las vistas de la clienta filtran
  `is_active=True`).
- Un plan aprobado no se edita. Aprobar uno nuevo reemplaza (`superseded`) al anterior.
- Toda función que recibe un entrenador comprueba la asignación (`clients_for_trainer`).

Nada de la ficha se escribe en logs: solo ids, códigos y estados.
"""
import dataclasses
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from apps.accounts.permissions import clients_for_trainer
from apps.assessments.services import latest_anthropometrics
from apps.exercises.models import Exercise
from apps.health import dates as health_dates
from apps.health import services as health
from apps.health.models import PURPOSE_HEALTH_DATA
from apps.routines.models import Routine, RoutineDay, RoutineExercise

from . import ai, notifications
from .models import KIND_EXERCISE, KIND_NUTRITION, Food, Plan, PlanEvent, PlanGenerationJob, TrainerCredential
from .rules import catalog as rules_catalog
from .rules import eligibility as rules_eligibility
from .rules import energy as rules_energy
from .rules import flags as rules_flags
from .rules import foods as rules_foods

logger = logging.getLogger(__name__)

PLAN_VALID_DAYS = 28                 # vigencia del plan aprobado (4 semanas)
WEEK = timedelta(days=7)
DAY = timedelta(days=1)
AVAILABLE_KINDS = (KIND_EXERCISE, KIND_NUTRITION)
TRAINER_NOTES_MAX = 500
ARCHIVE_REASON_MAX = 300
NOTE_FOR_CLIENT_MAX = 500


class PlanError(Exception):
    """Base de los errores de negocio de los planes (mensaje apto para mostrar)."""


class PlanNotAllowed(PlanError):
    pass


class PlanRequestNotReady(PlanError):
    def __init__(self, eligibility):
        super().__init__('Aún faltan requisitos para pedir el plan.')
        self.eligibility = eligibility


class PlanStateError(PlanError):
    pass


class QuotaExceeded(PlanError):
    pass


# ─── Disponibilidad y cupos ───────────────────────────────────────────────────

def feature_available():
    """¿Se ofrecen los planes con IA? Función de salud encendida, IA no apagada y textos
    legales listos (sin marcadores `[...]` en producción)."""
    return health.health_features_enabled() and ai.ai_enabled() and health.ai_texts_ready()


def _since(delta):
    return timezone.now() - delta


def _counted_jobs():
    # Los bloqueados por seguridad no cuentan (no llaman a la IA)
    return PlanGenerationJob.objects.exclude(status=PlanGenerationJob.STATUS_BLOCKED)


def global_quota_ok():
    return _counted_jobs().filter(created_at__gte=_since(DAY)).count() < settings.AI_PLANS_DAILY_JOB_LIMIT


def client_quota_ok(client):
    used = _counted_jobs().filter(
        client=client, requested_by_role=PlanGenerationJob.ROLE_MEMBER,
        kind__in=(KIND_EXERCISE, KIND_NUTRITION), created_at__gte=_since(WEEK),
    ).count()
    return used < settings.AI_PLANS_CLIENT_WEEKLY_LIMIT


def trainer_quota_ok(trainer):
    used = _counted_jobs().filter(
        requested_by=trainer, requested_by_role=PlanGenerationJob.ROLE_TRAINER, created_at__gte=_since(DAY),
    ).count()
    return used < settings.AI_PLANS_TRAINER_DAILY_LIMIT


def quota_ok(client, actor):
    if not global_quota_ok():
        return False
    if actor.is_trainer:
        return trainer_quota_ok(actor)
    return client_quota_ok(client)


# ─── Evaluación (¿se puede generar?) ──────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Assessment:
    eligibility: rules_eligibility.Eligibility
    report: rules_flags.FlagReport
    profile: object = None                       # HealthProfile vigente o None
    catalog: rules_catalog.CatalogResult | None = None          # ejercicios permitidos
    foods: rules_foods.FoodCatalog | None = None                # alimentos permitidos
    energy: rules_energy.EnergyPlan | None = None               # objetivos del día (alimentación)
    slots: tuple = ()                                           # objetivos por comida

    @property
    def blocked_message(self):
        """Texto para la clienta cuando está bloqueado (sin diagnósticos)."""
        for code in (rules_eligibility.D06_FEW_EXERCISES, rules_eligibility.D06_FEW_FOODS):
            if self.eligibility.red_codes == (code,):
                return rules_eligibility.REQUIREMENT_TEXTS[code]
        return rules_eligibility.BLOCKED_MESSAGE


def exercise_items():
    """Catálogo activo como datos simples para el motor (sin objetos de Django)."""
    rows = (Exercise.objects.filter(is_active=True).select_related('category')
            .only('id', 'name', 'level', 'equipment', 'risk_tags', 'intensity', 'category__name'))
    return [
        rules_catalog.ExerciseItem(
            id=row.id, name=row.name, category=row.category.name if row.category else '',
            level=row.level, equipment=tuple(row.equipment or ()), risk_tags=tuple(row.risk_tags or ()),
            intensity=row.intensity or '',
        )
        for row in rows
    ]


def training_profile(profile, yellow_codes):
    return rules_catalog.TrainingProfile(
        experience_level=profile.experience_level, training_place=profile.training_place,
        equipment=tuple(profile.equipment or ()), injuries=tuple(profile.injuries or ()),
        conditions=tuple(profile.conditions or ()), yellow_codes=tuple(yellow_codes),
    )


def food_items():
    """Catálogo de alimentos activo como datos simples para el motor. Fuera de DEBUG solo cuentan
    los alimentos validados por un profesional (`validated_by`), salvo que la validación esté suspendida."""
    rows = Food.objects.filter(is_active=True)
    if not settings.DEBUG and professional_required():
        rows = rows.exclude(validated_by='')
    return [
        rules_foods.FoodItem(
            id=row.id, name=row.name, group=row.group, kcal100=row.kcal100, protein100=row.protein100,
            carbs100=row.carbs100, fat100=row.fat100, fiber100=row.fiber100,
            household_unit=row.household_unit, household_unit_plural=row.household_unit_plural,
            household_grams=row.household_grams, allergen_tags=tuple(row.allergen_tags or ()),
            trace_tags=tuple(row.trace_tags or ()), diet_tags=tuple(row.diet_tags or ()),
            condition_tags=tuple(row.condition_tags or ()), is_common=row.is_common,
        )
        for row in rows
    ]


def nutrition_profile(profile, yellow_codes):
    return rules_foods.NutritionProfile(
        diet=profile.diet_type, allergies=tuple(profile.allergies or ()),
        intolerances=tuple(profile.intolerances or ()), conditions=tuple(profile.conditions or ()),
        yellow_codes=tuple(yellow_codes),
    )


def energy_and_slots(client, profile, yellow_codes):
    """Objetivos del día y por comida (Python, `Decimal`). `ValueError` si los datos no alcanzan."""
    anth = latest_anthropometrics(client)
    if anth is None or anth.height is None:
        raise ValueError('faltan peso o estatura')
    data = rules_energy.EnergyInput(
        age=health_dates.age_today(profile.birth_date), sex=profile.sex_for_calculation,
        weight_kg=anth.weight, height_m=anth.height, activity_level=profile.activity_level,
        goal=client.training_goal or '', diabetes_cap='Y02_DIABETES_T2' in yellow_codes,
        target_weight_kg=profile.target_weight_kg,
    )
    energy = rules_energy.compute_energy(data)
    return energy, tuple(rules_energy.split_meals(energy, profile.meal_slots))


def assess(client, kind, actor, check_quota=True):
    """Evalúa si se puede generar un plan `kind` para `client` pedido por `actor`.
    `check_quota=False` lo usa el trabajo ya creado (su cupo ya se contó)."""
    profile = health.current_profile(client)
    report = health.profile_flags(client, profile)
    eligibility = rules_eligibility.evaluate(
        report, kind,
        available=feature_available() and kind in AVAILABLE_KINDS,
        health_consent=health.has_valid_consent(client, PURPOSE_HEALTH_DATA),
        quota_ok=quota_ok(client, actor) if check_quota else True,
    )
    catalog_result = None
    if kind == KIND_EXERCISE and profile is not None and not eligibility.missing and not eligibility.red_codes:
        catalog_result = rules_catalog.filter_exercises(
            exercise_items(), training_profile(profile, eligibility.yellow_codes))
        if not catalog_result.enough:
            eligibility = dataclasses.replace(eligibility, red_codes=(rules_eligibility.D06_FEW_EXERCISES,))
    foods = energy = None
    slots = ()
    if kind == KIND_NUTRITION and profile is not None and not eligibility.missing and not eligibility.red_codes:
        foods = rules_foods.filter_foods(food_items(), nutrition_profile(profile, eligibility.yellow_codes))
        if foods.enough:
            try:
                energy, slots = energy_and_slots(client, profile, eligibility.yellow_codes)
            except ValueError:
                # Datos que el motor no puede calcular: se corrige en la ficha o las medidas
                eligibility = dataclasses.replace(eligibility, missing=(rules_eligibility.D04_INCOHERENT,))
        else:
            eligibility = dataclasses.replace(eligibility, red_codes=(rules_eligibility.D06_FEW_FOODS,))
    return Assessment(eligibility=eligibility, report=report, profile=profile, catalog=catalog_result,
                      foods=foods, energy=energy, slots=slots)


# ─── Pedidos (crean trabajos) ─────────────────────────────────────────────────

def _check_actor(client, actor):
    """La clienta pide para sí misma; un entrenador, solo para clientas que puede ver."""
    if actor.pk == client.pk:
        if client.is_trainer:
            raise PlanNotAllowed('Este usuario no tiene planes.')
        return PlanGenerationJob.ROLE_MEMBER
    if actor.is_trainer and clients_for_trainer(actor).filter(pk=client.pk).exists():
        return PlanGenerationJob.ROLE_TRAINER
    raise PlanNotAllowed('No tienes acceso a esta clienta.')


def _parse_token(request_token):
    try:
        return uuid.UUID(str(request_token))
    except (TypeError, ValueError, AttributeError):
        raise PlanError('El formulario expiró. Recarga la página e inténtalo de nuevo.') from None


def new_request_token():
    return str(uuid.uuid4())


def active_job(client, kind):
    return PlanGenerationJob.objects.filter(
        client=client, kind=kind, status__in=PlanGenerationJob.ACTIVE_STATUSES).first()


def request_plan(client, kind, actor, request_token, trainer_notes='', parent_plan=None):
    """Crea el trabajo de generación (o devuelve el existente). Devuelve `(job, created)`.

    - Mismo `request_token` (recarga, doble clic) -> el mismo trabajo.
    - Ya hay uno en curso para la clienta y el tipo -> ese.
    - Faltan requisitos -> `PlanRequestNotReady` (no se crea nada).
    - Bandera roja o sin ejercicios compatibles -> trabajo `blocked` (sin IA) y aviso al entrenador.
    """
    role = _check_actor(client, actor)
    token = _parse_token(request_token)
    existing = PlanGenerationJob.objects.filter(request_token=token).first()
    if existing is not None:
        if existing.client_id != client.pk:
            raise PlanError('El formulario expiró. Recarga la página e inténtalo de nuevo.')
        return existing, False
    if kind not in AVAILABLE_KINDS:
        raise PlanNotAllowed('Este tipo de plan aún no está disponible.')
    running = active_job(client, kind)
    if running is not None:
        return running, False

    from . import jobs   # evita el import circular (jobs usa este módulo)
    jobs.recover_stale_jobs()
    result = assess(client, kind, actor)
    eligibility = result.eligibility
    if eligibility.missing:
        if eligibility.missing == (rules_eligibility.D05_QUOTA,) and role == PlanGenerationJob.ROLE_TRAINER:
            raise QuotaExceeded('Llegaste al límite diario de generaciones. Inténtalo mañana.')
        raise PlanRequestNotReady(eligibility)

    notes = (trainer_notes or '')[:TRAINER_NOTES_MAX]
    common = dict(
        kind=kind, client=client, requested_by=actor, requested_by_role=role, request_token=token,
        health_profile=result.profile, parent_plan=parent_plan, trainer_notes=notes,
        max_attempts=settings.AI_PLANS_MAX_ATTEMPTS,
    )
    if eligibility.blocked:
        # Un solo aviso al día por el mismo bloqueo (los bloqueados no consumen cupo: sin esto,
        # cada clic enviaría otro correo al entrenador)
        recent = PlanGenerationJob.objects.filter(
            client=client, kind=kind, status=PlanGenerationJob.STATUS_BLOCKED,
            flags=list(eligibility.red_codes), created_at__gte=_since(DAY)).first()
        if recent is not None:
            return recent, False
        job = PlanGenerationJob.objects.create(
            status=PlanGenerationJob.STATUS_BLOCKED, flags=list(eligibility.red_codes),
            error_code='blocked', finished_at=timezone.now(), progress_total=1, **common,
        )
        logger.info('plan job blocked job_id=%s kind=%s codes=%s', job.pk, kind, ','.join(eligibility.red_codes))
        transaction.on_commit(lambda: notifications.trainer_job_blocked(job))
        return job, True
    try:
        with transaction.atomic():
            job = PlanGenerationJob.objects.create(
                status=PlanGenerationJob.STATUS_QUEUED, flags=list(eligibility.yellow_codes), **common)
    except IntegrityError:
        # Otro pedido simultáneo ganó la carrera (un solo trabajo en curso por clienta y tipo)
        running = active_job(client, kind) or PlanGenerationJob.objects.filter(request_token=token).first()
        if running is None:
            raise
        return running, False
    logger.info('plan job queued job_id=%s kind=%s role=%s', job.pk, kind, role)
    transaction.on_commit(lambda: jobs.start_job(job.pk))
    return job, True


# ─── Lectura ──────────────────────────────────────────────────────────────────

def plans_for_trainer(user):
    return Plan.objects.filter(client__in=clients_for_trainer(user)).select_related('client', 'routine')


def get_plan_for_trainer(user, pk):
    """El plan `pk` si su clienta es accesible; si no, `Plan.DoesNotExist` (la vista da 404)."""
    return plans_for_trainer(user).get(pk=pk)


def approved_plan(client, kind):
    return Plan.objects.filter(client=client, kind=kind, status=Plan.STATUS_APPROVED).select_related(
        'routine', 'approved_by').first()


def pending_plans(client):
    return Plan.objects.filter(client=client, status__in=Plan.PENDING_STATUSES)


def is_stale(plan):
    """La ficha cambió (o se borró) después de generar el plan."""
    current = health.current_profile(plan.client)
    return current is None or plan.health_profile_id != current.pk


def yellow_flags(plan):
    """Advertencias guardadas en el plan, con su texto para el entrenador."""
    return describe_codes(plan.flags)


def describe_codes(codes):
    """[{code, text, action}] de banderas (o del requisito D06) para el panel del entrenador."""
    rows = []
    for code in codes or ():
        if code in rules_eligibility.REQUIREMENT_TEXTS:
            text = rules_eligibility.REQUIREMENT_TEXTS[code]
            action = ('Armar la guía de alimentación aparte (con un nutricionista, si hace falta).'
                      if code == rules_eligibility.D06_FEW_FOODS else 'Armar el plan a mano en el constructor.')
        else:
            text, action = rules_flags.describe(code)
        rows.append({'code': code, 'text': text, 'action': action})
    return rows


def routine_conflicts(plan):
    """Ejercicios de la rutina que HOY no pasarían el filtro (p. ej. el entrenador agregó uno o la
    ficha cambió). Solo es una advertencia: el entrenador decide."""
    if plan.routine_id is None:
        return []
    profile = health.current_profile(plan.client)
    if profile is None:
        return []
    report = health.profile_flags(plan.client, profile)
    yellow = rules_eligibility.evaluate(report, plan.kind).yellow_codes
    allowed = rules_catalog.filter_exercises(exercise_items(), training_profile(profile, yellow)).allowed_ids
    names = []
    for item in RoutineExercise.objects.filter(day__routine=plan.routine).select_related('exercise'):
        if item.exercise_id not in allowed and item.exercise.name not in names:
            names.append(item.exercise.name)
    return names


def credential_for(user):
    return TrainerCredential.objects.filter(user=user).first()


def professional_required():
    """¿Se exige validación profesional (credencial de nutrición y alimentos validados)? Hoy suspendida
    (`AI_PLANS_REQUIRE_PROFESSIONAL=False`): se reactiva sin cambiar código."""
    return bool(getattr(settings, 'AI_PLANS_REQUIRE_PROFESSIONAL', False))


def can_validate_nutrition(user):
    if not professional_required():
        return True
    credential = credential_for(user)
    return bool(credential and credential.can_validate_nutrition)


# ─── Creación del borrador (la llama el trabajo con la salida YA validada) ────

def next_version(client, kind):
    return (Plan.objects.filter(client=client, kind=kind).aggregate(last=Max('version'))['last'] or 0) + 1


def create_exercise_draft(job, cleaned, *, model_name, prompt_version, is_simulated):
    """Crea la `Routine` inactiva y el `Plan` `draft_ai` desde la salida validada. Si el trabajo
    regeneraba otro plan, ese queda `archived`. Debe llamarse dentro de una transacción."""
    client = job.client
    trainer = client.assigned_trainer
    routine = Routine.objects.create(
        name=cleaned['routine_name'][:200], user=client, trainer=trainer,
        notes=cleaned['general_notes'], is_active=False,
    )
    for d_order, day in enumerate(cleaned['days']):
        routine_day = RoutineDay.objects.create(routine=routine, name=day['name'][:100], order=d_order)
        RoutineExercise.objects.bulk_create([
            RoutineExercise(day=routine_day, exercise_id=item['exercise_id'], sets=item['sets'],
                            reps=item['reps'][:50], rest_seconds=item['rest_seconds'],
                            observations=item['observations'], order=e_order)
            for e_order, item in enumerate(day['exercises'])
        ])
    profile = job.health_profile
    anth = latest_anthropometrics(client)
    plan = Plan.objects.create(
        client=client, kind=job.kind, version=next_version(client, job.kind), status=Plan.STATUS_DRAFT_AI,
        parent=job.parent_plan, routine=routine, health_profile=profile,
        profile_version=profile.version if profile else None,
        measurement=anth.measurement if anth else None, flags=list(job.flags or ()),
        is_simulated=is_simulated, model_name=model_name[:80], prompt_version=prompt_version,
        ruleset_version=rules_flags.RULESET_VERSION,
    )
    PlanEvent.objects.create(plan=plan, actor=job.requested_by, action=PlanEvent.ACTION_CREATED,
                             note='Borrador generado con IA' + (' (modo de prueba)' if is_simulated else ''))
    parent = job.parent_plan
    if parent is not None and parent.status in Plan.PENDING_STATUSES:
        _archive(parent, job.requested_by, 'Reemplazado por un borrador regenerado.')
    return plan


# ─── Revisión y aprobación (entrenador) ───────────────────────────────────────

def _check_trainer(plan, actor):
    if not (actor.is_trainer and clients_for_trainer(actor).filter(pk=plan.client_id).exists()):
        raise PlanNotAllowed('No tienes acceso a este plan.')


def take_for_review(plan, actor):
    """`draft_ai` -> `in_review` (al abrir para editar o con "Tomar para revisión")."""
    _check_trainer(plan, actor)
    updated = Plan.objects.filter(pk=plan.pk, status=Plan.STATUS_DRAFT_AI).update(
        status=Plan.STATUS_IN_REVIEW, reviewed_by=actor)
    if updated:
        PlanEvent.objects.create(plan=plan, actor=actor, action=PlanEvent.ACTION_TAKEN)
        plan.refresh_from_db()
    return bool(updated)


def _archive(plan, actor, reason):
    plan.status = Plan.STATUS_ARCHIVED
    plan.archived_at = timezone.now()
    plan.save(update_fields=['status', 'archived_at'])
    if plan.routine_id:
        Routine.objects.filter(pk=plan.routine_id).update(is_active=False)
    PlanEvent.objects.create(plan=plan, actor=actor, action=PlanEvent.ACTION_ARCHIVED, note=reason[:500])


def archive_plan(plan, actor, reason=''):
    """Descarta un borrador (o retira un plan aprobado: su rutina deja de verse)."""
    _check_trainer(plan, actor)
    with transaction.atomic():
        plan = Plan.objects.select_for_update().get(pk=plan.pk)
        if plan.status not in (*Plan.PENDING_STATUSES, Plan.STATUS_APPROVED):
            raise PlanStateError('Este plan ya no se puede archivar.')
        _archive(plan, actor, (reason or '').strip()[:ARCHIVE_REASON_MAX] or 'Archivado por el entrenador.')
    logger.info('plan archived plan_id=%s actor_id=%s', plan.pk, actor.pk)
    return plan


def approve_plan(plan, actor, *, note_for_client='', warnings_ack=False, stale_ack=False):
    """Aprueba un borrador: la clienta lo ve desde ahora. Reemplaza al aprobado anterior."""
    _check_trainer(plan, actor)
    if plan.status not in Plan.PENDING_STATUSES:
        raise PlanStateError('Solo se puede aprobar un borrador o un plan en revisión.')
    if plan.is_simulated and not settings.DEBUG:
        raise PlanStateError('Este plan se generó en MODO DE PRUEBA: no se puede aprobar. Genera uno real.')
    if plan.flags and not warnings_ack:
        raise PlanStateError('Marca "Revisé las advertencias" para aprobar.')
    if is_stale(plan) and not stale_ack:
        raise PlanStateError('La ficha cambió después de generar este plan: confirma que lo revisaste con la ficha actual.')
    if plan.kind == KIND_NUTRITION:
        from . import nutrition   # evita el import circular (nutrition usa este módulo)
        if not can_validate_nutrition(actor):
            raise PlanNotAllowed('Solo una persona con credencial de nutrición puede aprobar planes de alimentación.')
        problems = nutrition.approval_problems(plan)
        if problems:
            raise PlanStateError(problems[0])
    if plan.kind == KIND_EXERCISE:
        if plan.routine_id is None or not RoutineExercise.objects.filter(day__routine_id=plan.routine_id).exists():
            raise PlanStateError('La rutina no tiene ejercicios: agrégalos en el constructor antes de aprobar.')

    credential = credential_for(actor)
    now = timezone.now()
    with transaction.atomic():
        plan = Plan.objects.select_for_update().get(pk=plan.pk)
        if plan.status not in Plan.PENDING_STATUSES:
            raise PlanStateError('Este plan cambió mientras lo revisabas. Recarga la página.')
        previous = list(Plan.objects.select_for_update().filter(
            client=plan.client, kind=plan.kind, status=Plan.STATUS_APPROVED))
        for old in previous:
            old.status = Plan.STATUS_SUPERSEDED
            old.save(update_fields=['status'])
            if old.routine_id:
                Routine.objects.filter(pk=old.routine_id).update(is_active=False)
            PlanEvent.objects.create(plan=old, actor=actor, action=PlanEvent.ACTION_SUPERSEDED,
                                     note=f'Reemplazado por la versión {plan.version}.')
        plan.status = Plan.STATUS_APPROVED
        plan.approved_by = actor
        plan.approved_at = now
        plan.reviewed_by = plan.reviewed_by or actor
        plan.reviewer_credential = credential.registration_label if credential else ''
        plan.note_for_client = (note_for_client or '').strip()[:NOTE_FOR_CLIENT_MAX]
        plan.valid_until = health_dates.today() + timedelta(days=PLAN_VALID_DAYS)
        if plan.flags:
            plan.warnings_ack_by, plan.warnings_ack_at = actor, now
        plan.save()
        if plan.routine_id:
            Routine.objects.filter(pk=plan.routine_id).update(is_active=True)
        PlanEvent.objects.create(plan=plan, actor=actor, action=PlanEvent.ACTION_APPROVED)
        transaction.on_commit(lambda: notifications.client_plan_approved(plan))
    logger.info('plan approved plan_id=%s actor_id=%s', plan.pk, actor.pk)
    return plan


def regenerate_plan(plan, actor, notes, request_token):
    """Pide un borrador nuevo con las notas del entrenador; al terminar, el actual queda archivado."""
    _check_trainer(plan, actor)
    if plan.status not in Plan.PENDING_STATUSES:
        raise PlanStateError('Solo se regenera un borrador o un plan en revisión.')
    job, created = request_plan(plan.client, plan.kind, actor, request_token,
                                trainer_notes=notes, parent_plan=plan)
    if created:
        PlanEvent.objects.create(plan=plan, actor=actor, action=PlanEvent.ACTION_REGENERATE,
                                 note=(notes or '')[:500])
    return job, created
