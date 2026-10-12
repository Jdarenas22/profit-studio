"""Ejecución de los trabajos de generación (contrato, sección 6): cola en la base de datos + hilo
en el worker de gunicorn + recuperación de trabajos huérfanos. Sin Celery ni Redis.

1. `request_plan` crea el trabajo `queued` y, al confirmar la transacción, `start_job` lanza un
   hilo daemon (o lo corre en línea con `AI_PLANS_RUN_INLINE`).
2. El hilo RECLAMA el trabajo con un UPDATE atómico (`queued` -> `running`): si dos compiten,
   solo uno gana. Escribe `heartbeat_at` antes de cada llamada a la IA.
3. `recover_stale_jobs` devuelve a la cola los `running` sin latido de más de 3 minutos (worker
   reiniciado, redeploy) y relanza los `queued` olvidados. Corre al consultar un estado, al crear
   un trabajo y con `python manage.py process_plan_jobs`.

Reintentos: errores transitorios (429, 5xx, timeout) hasta 3 veces por llamada con espera
2/6/18 s + variación (respeta Retry-After); errores permanentes y bloqueos de seguridad no se
reintentan; una falla de validación se reintenta UNA vez enviando solo los códigos del error.
La salida no validada nunca se guarda. Presupuesto total: 4 minutos.

Logs: solo id, tipo, estado, tokens, latencia y código de error. Nunca prompts, respuestas,
datos de la ficha ni el cuerpo de error del proveedor.
"""
import logging
import random
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections, connection, transaction
from django.utils import timezone

from apps.health import dates as health_dates

from . import ai, notifications, nutrition, services
from .ai import prompts
from .models import KIND_EXERCISE, KIND_NUTRITION, PlanGenerationJob
from .rules import energy as rules_energy
from .rules import meal_validate as rules_meal
from .rules import validate as rules_validate

logger = logging.getLogger(__name__)

HEARTBEAT_STALE = timedelta(minutes=3)
QUEUED_FORGOTTEN = timedelta(minutes=1)
JOB_BUDGET_SECONDS = 4 * 60
TRANSIENT_TRIES = 3
TRANSIENT_WAITS = (2, 6, 18)
MAX_RETRY_AFTER = 30
VALIDATION_TRIES = 2          # la primera respuesta + un reintento con los códigos

_sleep = time.sleep           # las pruebas lo reemplazan


class JobFailed(Exception):
    def __init__(self, code, blocked=False):
        super().__init__(code)
        self.code = code
        self.blocked = blocked


# ─── Arranque, reclamo y recuperación ─────────────────────────────────────────

def start_job(job_id):
    """Ejecuta el trabajo en un hilo (o en línea con AI_PLANS_RUN_INLINE)."""
    if settings.AI_PLANS_RUN_INLINE:
        run_job(job_id)
        return
    thread = threading.Thread(target=_thread_main, args=(job_id,), name=f'plan-job-{job_id}', daemon=True)
    thread.start()


def _thread_main(job_id):
    close_old_connections()
    try:
        run_job(job_id)
    except Exception as exc:   # nunca dejar morir el hilo sin registro
        logger.error('plan job thread crashed job_id=%s error=%s', job_id, type(exc).__name__)
    finally:
        connection.close()


def claim(job_id):
    """`queued` -> `running` de forma atómica. True si este proceso lo ganó."""
    now = timezone.now()
    job = PlanGenerationJob.objects.filter(pk=job_id).only('attempts').first()
    if job is None:
        return False
    return bool(PlanGenerationJob.objects.filter(pk=job_id, status=PlanGenerationJob.STATUS_QUEUED).update(
        status=PlanGenerationJob.STATUS_RUNNING, locked_at=now, heartbeat_at=now,
        started_at=now, attempts=job.attempts + 1,
    ))


def recover_stale_jobs(start=True):
    """Recupera trabajos huérfanos. Devuelve los ids relanzados."""
    now = timezone.now()
    relaunch = []
    stale = PlanGenerationJob.objects.filter(
        status=PlanGenerationJob.STATUS_RUNNING, heartbeat_at__lt=now - HEARTBEAT_STALE)
    for job in stale:
        if job.attempts >= job.max_attempts:
            changed = PlanGenerationJob.objects.filter(pk=job.pk, status=PlanGenerationJob.STATUS_RUNNING,
                                                       heartbeat_at=job.heartbeat_at).update(
                status=PlanGenerationJob.STATUS_FAILED, error_code='worker_lost', finished_at=now)
            if changed:
                logger.warning('plan job failed job_id=%s error=worker_lost', job.pk)
        else:
            changed = PlanGenerationJob.objects.filter(pk=job.pk, status=PlanGenerationJob.STATUS_RUNNING,
                                                       heartbeat_at=job.heartbeat_at).update(
                status=PlanGenerationJob.STATUS_QUEUED, locked_at=None)
            if changed:
                logger.warning('plan job requeued job_id=%s', job.pk)
                relaunch.append(job.pk)
    forgotten = PlanGenerationJob.objects.filter(
        status=PlanGenerationJob.STATUS_QUEUED, created_at__lt=now - QUEUED_FORGOTTEN,
    ).exclude(pk__in=relaunch).values_list('pk', flat=True)
    relaunch.extend(forgotten)
    if start:
        for job_id in relaunch:
            start_job(job_id)
    return relaunch


def _heartbeat(job):
    PlanGenerationJob.objects.filter(pk=job.pk, status=PlanGenerationJob.STATUS_RUNNING).update(
        heartbeat_at=timezone.now())


def _finish(job, status, error_code='', plan=None):
    fields = dict(status=status, error_code=error_code[:80], finished_at=timezone.now(),
                  tokens_in=job.tokens_in, tokens_out=job.tokens_out, progress_done=job.progress_done)
    if plan is not None:
        fields['plan'] = plan
    PlanGenerationJob.objects.filter(pk=job.pk, status=PlanGenerationJob.STATUS_RUNNING).update(**fields)


# ─── Ejecución ────────────────────────────────────────────────────────────────

def run_job(job_id):
    """Corre un trabajo hasta el final. Nunca lo deja en `running` si no se cae el proceso."""
    if not claim(job_id):
        return None
    job = PlanGenerationJob.objects.select_related('client', 'health_profile', 'parent_plan',
                                                   'requested_by').get(pk=job_id)
    started = time.monotonic()
    try:
        runner = _RUNNERS.get(job.kind)
        if runner is None:
            raise JobFailed('kind_not_available')
        plan = runner(job, started)
    except JobFailed as exc:
        status = PlanGenerationJob.STATUS_BLOCKED if exc.blocked else PlanGenerationJob.STATUS_FAILED
        _finish(job, status, exc.code)
        _log(job, status, started, exc.code)
        notify = notifications.trainer_job_blocked if exc.blocked else notifications.trainer_job_failed
        notify(job)
        return None
    except Exception as exc:
        _finish(job, PlanGenerationJob.STATUS_FAILED, 'internal_error')
        logger.exception('plan job internal error job_id=%s error=%s', job.pk, type(exc).__name__)
        notifications.trainer_job_failed(job)
        return None
    _log(job, PlanGenerationJob.STATUS_SUCCEEDED, started)
    if job.kind == nutrition.ALTERNATIVE:
        notifications.trainer_alternative_ready(plan)
    else:
        notifications.trainer_draft_ready(plan)
    return plan


def _log(job, status, started, error_code=''):
    logger.info('plan job finished job_id=%s kind=%s status=%s tokens_in=%s tokens_out=%s latency_ms=%s error=%s',
                job.pk, job.kind, status, job.tokens_in, job.tokens_out,
                int((time.monotonic() - started) * 1000), error_code or '-')


def _check_budget(started):
    if time.monotonic() - started > JOB_BUDGET_SECONDS:
        raise JobFailed('timeout')


def build_exercise_payload(job, assessment, previous_errors=()):
    """Lo ÚNICO que viaja a la IA (contrato, sección 4.4): sin nombre, correo, teléfono, usuario,
    id de usuario, fecha de nacimiento, entrenador, diagnósticos ni texto libre de la ficha."""
    profile = assessment.profile
    catalog = assessment.catalog
    limits = catalog.limits
    max_per_day = max(rules_validate.MIN_EXERCISES_PER_DAY,
                      min(rules_validate.MAX_EXERCISES_PER_DAY,
                          profile.session_minutes // rules_validate.MINUTES_PER_EXERCISE))
    return {
        'task': 'exercise_plan',
        'schema_version': rules_validate.EXERCISE_SCHEMA_VERSION,
        'job_ref': str(job.job_ref),
        'client': {
            'age': health_dates.age_today(profile.birth_date),
            'sex': profile.sex_for_calculation,
            'goal': job.client.training_goal or '',
            'experience': profile.experience_level,
            'activity': profile.activity_level,
            'conservative': assessment.eligibility.conservative,
        },
        'training': {
            'days_per_week': profile.training_days_per_week,
            'session_minutes': profile.session_minutes,
            'place': profile.training_place,
        },
        'limits': {
            'exercises_per_day_min': rules_validate.MIN_EXERCISES_PER_DAY,
            'exercises_per_day_max': max_per_day,
            'sets_max': limits.max_sets,
            'rest_seconds_min': rules_validate.MIN_REST,
            'rest_seconds_max': rules_validate.MAX_REST,
            'intensity_max': limits.max_intensity,
        },
        'allowed_exercises': [
            {'id': item.id, 'name': item.name, 'category': item.category, 'level': item.level,
             'intensity': item.intensity or 'sin_etiqueta'}
            for item in catalog.allowed
        ],
        'trainer_notes': job.trainer_notes or None,
        'previous_errors': list(previous_errors),
    }


def _call_with_retries(client, job, started, **kwargs):
    """Una llamada a la IA con reintentos por errores transitorios."""
    for attempt in range(1, TRANSIENT_TRIES + 1):
        _check_budget(started)
        _heartbeat(job)
        try:
            return client.generate_json(**kwargs)
        except ai.AITransientError as exc:
            if attempt == TRANSIENT_TRIES:
                raise JobFailed(exc.code) from None
            wait = TRANSIENT_WAITS[attempt - 1] + random.uniform(0, 1)
            if exc.retry_after:
                wait = max(wait, min(exc.retry_after, MAX_RETRY_AFTER))
            _sleep(wait)
        except ai.AIBlockedError as exc:
            raise JobFailed(exc.code) from None
        except ai.AIPermanentError as exc:
            raise JobFailed(exc.code) from None
    raise JobFailed('ai_unknown')   # no se llega aquí


def _generate_validated(job, started, ai_client, *, task, system, make_payload, make_schema, validate):
    """Una llamada a la IA con validación: la primera respuesta y UN reintento enviando solo los
    códigos del error. Devuelve `(validación_ok, resultado_de_la_IA)` o lanza `JobFailed`."""
    previous_errors = ()
    last_code = 'validation_failed'
    for _try in range(VALIDATION_TRIES):
        payload = make_payload(previous_errors)
        try:
            result = _call_with_retries(
                ai_client, job, started, task=task, system=system, payload=payload,
                schema=make_schema(payload), job_ref=str(job.job_ref))
        except ai.AIInvalidOutput:
            previous_errors, last_code = (rules_validate.V01_SCHEMA,), f'validation_failed:{rules_validate.V01_SCHEMA}'
            continue
        job.tokens_in += result.usage.tokens_in
        job.tokens_out += result.usage.tokens_out
        if result.finish_reason != ai.base.FINISH_NORMAL:
            previous_errors, last_code = (rules_validate.V01_SCHEMA,), f'ai_finish_{result.finish_reason.lower()}'[:80]
            continue
        validation = validate(result.data)
        if not validation.ok:
            previous_errors, last_code = validation.codes, validation.error_code
            continue
        return validation, result
    raise JobFailed(last_code)


def _evaluate_for_run(job, kind):
    """Se vuelve a evaluar al ejecutar: la ficha, los consentimientos o el catálogo pudieron cambiar."""
    assessment = services.assess(job.client, kind, job.requested_by or job.client, check_quota=False)
    eligibility = assessment.eligibility
    if eligibility.missing:
        raise JobFailed(f'not_ready:{eligibility.missing[0]}')
    if eligibility.red_codes:
        PlanGenerationJob.objects.filter(pk=job.pk).update(flags=list(eligibility.red_codes))
        raise JobFailed('blocked', blocked=True)
    profile = assessment.profile
    if job.health_profile_id != profile.pk:
        job.health_profile = profile
        PlanGenerationJob.objects.filter(pk=job.pk).update(health_profile=profile)
    job.flags = list(eligibility.yellow_codes)
    return assessment


def _run_exercise(job, started):
    assessment = _evaluate_for_run(job, job.kind)
    profile, catalog = assessment.profile, assessment.catalog
    PlanGenerationJob.objects.filter(pk=job.pk).update(flags=job.flags, progress_total=1)

    client = ai.get_ai_client()
    rules = rules_validate.ExerciseRules(
        allowed_ids=catalog.allowed_ids, days=profile.training_days_per_week,
        session_minutes=profile.session_minutes, max_sets=catalog.limits.max_sets,
    )
    validation, result = _generate_validated(
        job, started, client, task='exercise_plan', system=prompts.EXERCISE_SYSTEM,
        make_payload=lambda errors: build_exercise_payload(job, assessment, errors),
        make_schema=lambda payload: prompts.exercise_schema(
            catalog.allowed_ids, profile.training_days_per_week,
            payload['limits']['exercises_per_day_max'], catalog.limits.max_sets),
        validate=lambda data: rules_validate.validate_exercise_plan(data, rules))
    _check_budget(started)
    with transaction.atomic():
        locked = PlanGenerationJob.objects.select_for_update().get(pk=job.pk)
        if locked.status != PlanGenerationJob.STATUS_RUNNING:
            raise JobFailed('lost_lock')
        plan = services.create_exercise_draft(
            job, validation.data, model_name=result.model or client.model_name,
            prompt_version=prompts.EXERCISE_PROMPT_VERSION, is_simulated=client.is_simulated)
        job.progress_done = 1
        _finish(job, PlanGenerationJob.STATUS_SUCCEEDED, plan=plan)
    return plan


# ─── Alimentación ─────────────────────────────────────────────────────────────

def build_meal_payload(job, assessment, slot, *, n_options, existing, avoid_names, previous_errors=()):
    """Lo ÚNICO que viaja a la IA para una comida (contrato, 4.4 y 4.5): sin nombre, correo,
    teléfono, usuario, ids de usuario, fecha de nacimiento, entrenador, alergias, diagnósticos ni
    texto libre de la ficha (las restricciones ya se aplicaron al filtrar los alimentos)."""
    profile = assessment.profile
    return {
        'task': 'meal_slot',
        'schema_version': rules_meal.MEAL_SCHEMA_VERSION,
        'job_ref': str(job.job_ref),
        'slot': {'code': slot.code, 'target_kcal': int(slot.kcal), 'protein_g': int(slot.protein_g),
                 'carbs_g': int(slot.carbs_g), 'fat_g': int(slot.fat_g)},
        'options_requested': n_options,
        'client': {
            'age': health_dates.age_today(profile.birth_date),
            'sex': profile.sex_for_calculation,
            'goal': job.client.training_goal or '',
            'diet': profile.diet_type,
            'cooking': profile.cooking_access,
            'budget': profile.budget_level,
            'conservative': assessment.eligibility.conservative,
        },
        'allowed_foods': [
            {'id': food.id, 'name': food.name, 'group': food.group, 'kcal100': float(food.kcal100),
             'p100': float(food.protein100), 'c100': float(food.carbs100), 'f100': float(food.fat100),
             'common': food.is_common}
            for food in assessment.foods.allowed
        ],
        'existing_options': [{'name': option.name, 'food_ids': sorted(option.food_ids)} for option in existing],
        'avoid_repeating_names': list(avoid_names),
        'trainer_notes': job.trainer_notes or None,
        'previous_errors': list(previous_errors),
    }


class _SlotView:
    """Objetivos de una comida con la misma forma para el plan nuevo y para "otra opción"."""

    def __init__(self, code, kcal, protein_g, carbs_g, fat_g):
        self.code, self.kcal, self.protein_g, self.carbs_g, self.fat_g = code, kcal, protein_g, carbs_g, fat_g


def _generate_slot_options(job, started, ai_client, assessment, slot, *, n_options, existing=(), avoid_names=()):
    rules = nutrition.slot_rules(slot.code, slot.kcal, slot.protein_g, assessment, n_options=n_options,
                                 existing=existing)
    validation, result = _generate_validated(
        job, started, ai_client, task='meal_slot', system=prompts.MEAL_SYSTEM,
        make_payload=lambda errors: build_meal_payload(
            job, assessment, slot, n_options=n_options, existing=existing, avoid_names=avoid_names,
            previous_errors=errors),
        make_schema=lambda payload: prompts.meal_schema(assessment.foods.allowed_ids, slot.code, n_options),
        validate=lambda data: rules_meal.validate_meal_slot(data, rules))
    return validation.data['options'], result


def _run_nutrition(job, started):
    assessment = _evaluate_for_run(job, KIND_NUTRITION)
    slots = assessment.slots
    PlanGenerationJob.objects.filter(pk=job.pk).update(flags=job.flags, progress_total=len(slots), progress_done=0)

    client = ai.get_ai_client()
    generated, names, model = [], [], ''
    for index, target in enumerate(slots, start=1):
        _check_budget(started)
        options, result = _generate_slot_options(job, started, client, assessment, target, n_options=3,
                                                 avoid_names=names)
        model = model or result.model
        names.extend(option['name'] for option in options)
        generated.append((target, options))
        job.progress_done = index
        PlanGenerationJob.objects.filter(pk=job.pk, status=PlanGenerationJob.STATUS_RUNNING).update(progress_done=index)

    # Segunda comprobación sobre el día completo (toda combinación de opciones)
    energy = assessment.energy
    day_codes = rules_meal.check_day(
        [[option['totals'] for option in options] for _target, options in generated],
        energy.kcal, energy.protein_g, rules_energy.KCAL_FLOOR[assessment.profile.sex_for_calculation])
    if day_codes:
        raise JobFailed(f'validation_failed:{day_codes[0]}')
    _check_budget(started)
    with transaction.atomic():
        locked = PlanGenerationJob.objects.select_for_update().get(pk=job.pk)
        if locked.status != PlanGenerationJob.STATUS_RUNNING:
            raise JobFailed('lost_lock')
        plan = nutrition.create_nutrition_draft(
            job, assessment, generated, model_name=model or client.model_name,
            prompt_version=prompts.MEAL_PROMPT_VERSION, is_simulated=client.is_simulated)
        _finish(job, PlanGenerationJob.STATUS_SUCCEEDED, plan=plan)
    return plan


def _run_alternative(job, started):
    """"Otra opción": una sola opción nueva para una comida del plan aprobado."""
    slot, replaced = job.slot, job.replaces_option
    if slot is None or replaced is None:
        raise JobFailed('slot_missing')
    plan = slot.nutrition_plan.plan
    if plan.status != plan.STATUS_APPROVED or plan.client_id != job.client_id:
        raise JobFailed('plan_not_active')
    assessment = _evaluate_for_run(job, KIND_NUTRITION)
    PlanGenerationJob.objects.filter(pk=job.pk).update(flags=job.flags, progress_total=1)

    others = list(slot.options.filter(is_active=True).prefetch_related('items'))
    existing = [rules_meal.ExistingOption(name=o.name, food_ids=frozenset(i.food_id for i in o.items.all()))
                for o in others]       # incluye la rechazada: la nueva no debe parecérsele
    target = _SlotView(slot.code, slot.kcal_target, slot.protein_g, slot.carbs_g, slot.fat_g)
    client = ai.get_ai_client()
    options, result = _generate_slot_options(job, started, client, assessment, target, n_options=1,
                                             existing=existing, avoid_names=[o.name for o in others])
    _check_budget(started)
    with transaction.atomic():
        locked = PlanGenerationJob.objects.select_for_update().get(pk=job.pk)
        if locked.status != PlanGenerationJob.STATUS_RUNNING:
            raise JobFailed('lost_lock')
        if nutrition.create_alternative(job, options[0], plan=plan, slot=slot, replaced=replaced) is None:
            raise JobFailed('option_changed')
        job.progress_done = 1
        _finish(job, PlanGenerationJob.STATUS_SUCCEEDED, plan=plan)
    return plan


_RUNNERS = {
    KIND_EXERCISE: _run_exercise,
    KIND_NUTRITION: _run_nutrition,
    nutrition.ALTERNATIVE: _run_alternative,
}
