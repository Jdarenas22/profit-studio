"""Casos de uso del plan de alimentación (fase C de docs/contracts/plan-ia.md, secciones 3.4-3.6 y 7).

Reglas:
- La IA elige alimentos del catálogo y gramos; Python calcula energía, macros y medidas caseras.
- Con bandera roja o sin alimentos suficientes (`D06_FEW_FOODS`) no se llama a la IA.
- El plan nace como borrador (`draft_ai`) con 3 opciones equivalentes por comida; la clienta solo
  ve planes `approved`, y aprobar exige una persona con credencial de nutrición.
- "Otra opción": la clienta pide reemplazar UNA opción de su plan aprobado. La nueva pasa las
  mismas validaciones deterministas, queda marcada para revisión del entrenador y no bloquea a la
  clienta. Cupos contados en la base.
- Toda función que recibe un entrenador comprueba la asignación (`clients_for_trainer`).

Nada de la ficha se escribe en logs: solo ids, códigos y estados.
"""
import logging
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.utils import timezone

from apps.assessments.services import latest_anthropometrics
from apps.health import dates as health_dates
from apps.health import services as health

from . import notifications, services
from .models import (
    KIND_NUTRITION, MealChoice, MealOption, MealOptionItem, MealSlot, NutritionPlan, Plan, PlanEvent,
    PlanGenerationJob,
)
from .rules import eligibility as rules_eligibility
from .rules import energy as rules_energy
from .rules import flags as rules_flags
from .rules import foods as rules_foods
from .rules import meal_validate as rules_meal

logger = logging.getLogger(__name__)

D = Decimal
MEAL_WINDOW_DAYS = 7              # la clienta elige para hoy y los 6 días siguientes
ALTERNATIVE = PlanGenerationJob.KIND_MEAL_ALTERNATIVE
OPTION_POSITIONS = (1, 2, 3)


class OptionInvalid(services.PlanError):
    """La opción editada no pasa la validación (`errors` = [(código, detalle)])."""

    def __init__(self, errors):
        super().__init__('La opción no es válida.')
        self.errors = list(errors)


# ─── Reglas de una comida ─────────────────────────────────────────────────────

def slot_rules(code, kcal, protein_g, assessment, n_options=rules_meal.OPTIONS_PER_SLOT, existing=()):
    """`SlotRules` de una comida con los alimentos permitidos de la evaluación."""
    nutrition = services.nutrition_profile(assessment.profile, assessment.eligibility.yellow_codes)
    return rules_meal.SlotRules(
        slot_code=code, kcal=D(kcal), protein_g=D(protein_g), foods=assessment.foods.by_id,
        blocked_allergens=tuple(sorted(rules_foods.excluded_allergens(nutrition))),
        diet=nutrition.diet, n_options=n_options, existing=tuple(existing),
    )


def _dec1(value):
    return D(value).quantize(D('0.1'), rounding=ROUND_HALF_UP)


def _save_option(slot, position, data, *, origin, needs_review=False, replaces=None, out_of_band=False):
    totals = data['totals']
    option = MealOption.objects.create(
        slot=slot, position=position, name=data['name'][:120], preparation=data['preparation'][:500],
        kcal=_dec1(totals.kcal), protein_g=_dec1(totals.protein), carbs_g=_dec1(totals.carbs),
        fat_g=_dec1(totals.fat), origin=origin, needs_review=needs_review, replaces=replaces,
        out_of_band=out_of_band,
    )
    MealOptionItem.objects.bulk_create([
        MealOptionItem(option=option, food_id=item['food_id'], grams=item['grams'], order=order)
        for order, item in enumerate(data['items'])
    ])
    return option


# ─── Creación del borrador (la llama el trabajo con TODA la salida ya validada) ─

def create_nutrition_draft(job, assessment, generated, *, model_name, prompt_version, is_simulated):
    """`generated` = [(SlotTarget, [opción validada, ...])] en el orden del día. Crea el `Plan`
    `draft_ai`, sus objetivos, comidas y opciones. Debe llamarse dentro de una transacción."""
    client, profile, energy = job.client, assessment.profile, assessment.energy
    anth = latest_anthropometrics(client)
    plan = Plan.objects.create(
        client=client, kind=KIND_NUTRITION, version=services.next_version(client, KIND_NUTRITION),
        status=Plan.STATUS_DRAFT_AI, parent=job.parent_plan, health_profile=profile,
        profile_version=profile.version, measurement=anth.measurement if anth else None,
        flags=list(job.flags or ()), is_simulated=is_simulated, model_name=model_name[:80],
        prompt_version=prompt_version, ruleset_version=f'{rules_flags.RULESET_VERSION};{energy.formula}'[:40],
    )
    nutrition = NutritionPlan.objects.create(
        plan=plan, kcal_target=int(energy.kcal), protein_g=int(energy.protein_g), carbs_g=int(energy.carbs_g),
        fat_g=int(energy.fat_g), fiber_g=int(energy.fiber_g), tmb=energy.tmb, get=int(energy.get),
        activity_factor=energy.activity_factor, adjustment_pct=energy.adjustment_pct,
        weekly_change_kg=energy.weekly_change_kg, floor_applied=energy.floor_applied,
        warnings=list(energy.warnings), meals_per_day=len(generated), formula=energy.formula,
    )
    times = profile.meal_times or {}
    for target, options in generated:
        slot = MealSlot.objects.create(
            nutrition_plan=nutrition, code=target.code, name=target.name, order=target.order,
            time_hint=str(times.get(target.code, ''))[:10], kcal_target=int(target.kcal),
            protein_g=int(target.protein_g), carbs_g=int(target.carbs_g), fat_g=int(target.fat_g),
        )
        for position, option in zip(OPTION_POSITIONS, options):
            _save_option(slot, position, option, origin=MealOption.ORIGIN_AI)
    PlanEvent.objects.create(plan=plan, actor=job.requested_by, action=PlanEvent.ACTION_CREATED,
                             note='Borrador generado con IA' + (' (modo de prueba)' if is_simulated else ''))
    parent = job.parent_plan
    if parent is not None and parent.status in Plan.PENDING_STATUSES:
        services._archive(parent, job.requested_by, 'Reemplazado por un borrador regenerado.')
    return plan


# ─── Lectura con medidas caseras ──────────────────────────────────────────────

def _option_view(option):
    items = [{'food': item.food, 'grams': item.grams,
              'household': rules_foods.household_text(item.food, item.grams)} for item in option.items.all()]
    return {'option': option, 'items': items, 'letter': 'ABC'[option.position - 1] if option.position in (1, 2, 3) else ''}


def plan_slots(plan, only_active=True):
    """Comidas del plan con sus opciones (items, medidas caseras y macros ya calculados)."""
    options = MealOption.objects.order_by('position', 'pk').prefetch_related('items__food')
    if only_active:
        options = options.filter(is_active=True)
    slots = plan.nutrition.slots.prefetch_related(Prefetch('options', queryset=options, to_attr='shown_options'))
    return [{'slot': slot, 'options': [_option_view(o) for o in slot.shown_options]} for slot in slots]


def macros_row(totals_like):
    return rules_foods.round_totals(totals_like)


# ─── Revisión y aprobación ────────────────────────────────────────────────────

def current_allowed_ids(plan):
    """Ids de alimentos permitidos HOY para la clienta del plan (o None si no se puede evaluar)
    y las banderas rojas vigentes de alimentación."""
    client = plan.client
    profile = health.current_profile(client)
    if profile is None:
        return None, ()
    report = health.profile_flags(client, profile)
    eligibility = rules_eligibility.evaluate(report, KIND_NUTRITION)
    foods = rules_foods.filter_foods(services.food_items(), services.nutrition_profile(profile, eligibility.yellow_codes))
    return foods.allowed_ids, eligibility.red_codes


def conflicting_option_ids(plan):
    """Ids de las opciones activas con algún alimento que hoy NO es compatible con la clienta
    (alérgenos, dieta, etiquetas de las advertencias o alimento que ya no está disponible)."""
    if plan.kind != KIND_NUTRITION:
        return set()
    allowed, _red = current_allowed_ids(plan)
    if allowed is None:
        return set()
    return set(MealOptionItem.objects.filter(
        option__slot__nutrition_plan__plan=plan, option__is_active=True,
    ).exclude(food_id__in=allowed).values_list('option_id', flat=True))


def option_conflicts(plan):
    """Nombres de las opciones activas que hoy no son compatibles con la clienta."""
    ids = conflicting_option_ids(plan)
    if not ids:
        return []
    return list(MealOption.objects.filter(pk__in=ids).order_by('slot__order', 'position').values_list('name', flat=True))


def approval_problems(plan):
    """Motivos que BLOQUEAN la aprobación de un plan de alimentación (además de los generales)."""
    problems = []
    nutrition = NutritionPlan.objects.filter(plan=plan).first()
    if nutrition is None:
        return ['El plan no tiene objetivos calculados.']
    for slot in nutrition.slots.all():
        positions = sorted(slot.options.filter(is_active=True).values_list('position', flat=True))
        if positions != list(OPTION_POSITIONS):
            problems.append(f'La comida «{slot.name}» no tiene exactamente 3 opciones activas.')
    _allowed, red = current_allowed_ids(plan)
    if red:
        problems.append('La ficha actual de la clienta requiere valoración profesional antes de aprobar un plan '
                        'de alimentación. Archívalo y contáctala.')
    conflicts = option_conflicts(plan)
    if conflicts:
        problems.append('Hay opciones con alimentos que hoy no son compatibles con la clienta: '
                        + ', '.join(conflicts) + '.')
    return problems


def review_context(plan):
    """Lo que el entrenador necesita para revisar el plan de alimentación."""
    nutrition = plan.nutrition
    slots = plan_slots(plan, only_active=True)
    return {
        'nutrition': nutrition,
        'warnings': [rules_energy.WARNING_TEXTS[c] for c in nutrition.warnings if c in rules_energy.WARNING_TEXTS],
        'slots': slots,
        'conflicts': option_conflicts(plan) if (plan.is_pending or plan.is_approved) else [],
        'needs_review_count': sum(1 for s in slots for o in s['options'] if o['option'].needs_review),
        'unvalidated_foods': MealOptionItem.objects.filter(
            option__slot__nutrition_plan__plan=plan, option__is_active=True, food__validated_by='').exists(),
    }


# ─── Edición de una opción por el entrenador ──────────────────────────────────

def edit_option(plan, option, actor, name, preparation, items):
    """El entrenador cambia alimentos y gramos de una opción de un borrador. Bloquean los errores
    de forma, catálogo, texto y alérgenos; la energía/proteína fuera de banda solo avisa."""
    services._check_trainer(plan, actor)
    if plan.kind != KIND_NUTRITION or not (plan.is_pending or (plan.is_approved and option.needs_review)):
        raise services.PlanStateError('Solo se editan las opciones de un borrador, de un plan en revisión o '
                                      'una opción nueva pedida por la clienta.')
    slot = option.slot
    if slot.nutrition_plan.plan_id != plan.pk or not option.is_active:
        raise services.PlanStateError('Esa opción ya no está activa en el plan.')
    assessment = services.assess(plan.client, KIND_NUTRITION, actor, check_quota=False)
    if assessment.foods is None:
        raise services.PlanStateError('No se puede validar la opción: faltan requisitos de la clienta o su '
                                      'ficha requiere valoración profesional.')
    rules = slot_rules(slot.code, slot.kcal_target, slot.protein_g, assessment)
    result = rules_meal.validate_trainer_option(name, preparation, items, rules)
    if not result.ok:
        raise OptionInvalid(result.errors)
    with transaction.atomic():
        option = MealOption.objects.select_for_update().get(pk=option.pk)
        option.items.all().delete()
        MealOptionItem.objects.bulk_create([
            MealOptionItem(option=option, food_id=item['food_id'], grams=item['grams'], order=order)
            for order, item in enumerate(result.items)
        ])
        totals = result.totals
        option.name, option.preparation = result.name[:120], result.preparation[:500]
        option.kcal, option.protein_g = _dec1(totals.kcal), _dec1(totals.protein)
        option.carbs_g, option.fat_g = _dec1(totals.carbs), _dec1(totals.fat)
        option.origin, option.needs_review, option.out_of_band = MealOption.ORIGIN_TRAINER, False, result.out_of_band
        option.save()
        services.take_for_review(plan, actor)
        PlanEvent.objects.create(plan=plan, actor=actor, action=PlanEvent.ACTION_EDITED,
                                 note=f'Editó una opción de {slot.name}.')
    return option, result


def review_option(plan, option, actor, action):
    """El entrenador revisa una opción nueva pedida por la clienta: `keep` la deja (queda revisada)
    o `revert` la quita y vuelve a activar la que la clienta había rechazado."""
    services._check_trainer(plan, actor)
    if plan.kind != KIND_NUTRITION or option.slot.nutrition_plan.plan_id != plan.pk:
        raise services.PlanStateError('Esa opción no es de este plan.')
    if not (option.is_active and option.needs_review):
        raise services.PlanStateError('Esa opción ya fue revisada.')
    with transaction.atomic():
        option = MealOption.objects.select_for_update().get(pk=option.pk)
        if action == 'revert':
            previous = option.replaces
            if previous is None:
                raise services.PlanStateError('No hay una opción anterior para recuperar.')
            option.is_active, option.needs_review = False, False
            option.save(update_fields=['is_active', 'needs_review'])
            previous.is_active = True
            previous.save(update_fields=['is_active'])
            note = f'Descartó la opción nueva de {option.slot.name} y recuperó la anterior.'
        else:
            option.needs_review = False
            option.save(update_fields=['needs_review'])
            note = f'Revisó la opción nueva de {option.slot.name}.'
        PlanEvent.objects.create(plan=plan, actor=actor, action=PlanEvent.ACTION_EDITED, note=note)
    return option


# ─── La clienta: ver, elegir ──────────────────────────────────────────────────

def member_plan(client):
    plan = services.approved_plan(client, KIND_NUTRITION)
    if plan is None or not hasattr(plan, 'nutrition'):
        return None
    return plan


def meal_dates():
    today = health_dates.today()
    return [today + timedelta(days=offset) for offset in range(MEAL_WINDOW_DAYS)]


def parse_meal_date(raw):
    """La fecha pedida si está en la ventana de 7 días; si no, hoy."""
    dates = meal_dates()
    try:
        value = datetime.strptime(raw or '', '%Y-%m-%d').date()
    except ValueError:
        return dates[0]
    return value if value in dates else dates[0]


def alternatives_left(client, slot):
    base = PlanGenerationJob.objects.filter(
        client=client, kind=ALTERNATIVE, requested_by_role=PlanGenerationJob.ROLE_MEMBER,
    ).exclude(status=PlanGenerationJob.STATUS_BLOCKED)
    week = base.filter(slot=slot, created_at__gte=services._since(services.WEEK)).count()
    day = base.filter(created_at__gte=services._since(services.DAY)).count()
    return (max(0, settings.AI_PLANS_ALT_SLOT_WEEKLY_LIMIT - week), max(0, settings.AI_PLANS_ALT_DAILY_LIMIT - day))


def day_view(client, plan, on_date):
    """Comidas con 3 opciones, lo elegido ese día y el total estimado contra el objetivo."""
    slots = plan_slots(plan, only_active=True)
    conflicts = conflicting_option_ids(plan)
    for entry in slots:
        for opt in entry['options']:
            opt['conflict'] = opt['option'].id in conflicts
    chosen = {c.slot_id: c for c in MealChoice.objects.filter(
        client=client, date=on_date, slot__nutrition_plan__plan=plan, option__is_active=True)}
    total = rules_foods.Totals()
    picked = 0
    for entry in slots:
        slot = entry['slot']
        choice = chosen.get(slot.id)
        entry['chosen_id'] = choice.option_id if choice else None
        entry['week_left'], entry['day_left'] = alternatives_left(client, slot)
        if choice:
            picked += 1
            option = choice.option
            total = total + rules_foods.Totals(option.kcal, option.protein_g, option.carbs_g, option.fat_g)
    nutrition = plan.nutrition
    return {
        'slots': slots, 'chosen_count': picked, 'slot_count': len(slots),
        'day_totals': rules_foods.round_totals(total) if picked else None,
        'targets': {'kcal': nutrition.kcal_target, 'protein': nutrition.protein_g,
                    'carbs': nutrition.carbs_g, 'fat': nutrition.fat_g},
    }


class OptionNotFound(LookupError):
    """La comida u opción no pertenece al plan aprobado de la clienta (la vista responde 404)."""


def _member_slot_option(client, slot_pk, option_pk):
    plan = member_plan(client)
    if plan is None:
        raise OptionNotFound
    slot = MealSlot.objects.filter(pk=slot_pk, nutrition_plan__plan=plan).first()
    option = MealOption.objects.filter(pk=option_pk, slot=slot, is_active=True).first() if slot else None
    if slot is None or option is None:
        raise OptionNotFound
    return plan, slot, option


def choose_option(client, slot_pk, option_pk, on_date):
    """Guarda la opción elegida para una comida de un día (cambiarla el mismo día es válido)."""
    plan, slot, option = _member_slot_option(client, slot_pk, option_pk)
    if on_date not in meal_dates():
        raise services.PlanStateError('Elige un día entre hoy y los próximos 6 días.')
    if option.pk in conflicting_option_ids(plan):
        raise services.PlanStateError('Esa opción incluye un alimento que ya no es compatible con tu ficha. '
                                      'Pide otra opción o habla con tu entrenador.')
    choice, created = MealChoice.objects.get_or_create(
        client=client, slot=slot, date=on_date, defaults={'option': option})
    if not created and choice.option_id != option.pk:
        choice.option, choice.completed_at = option, None
        choice.save(update_fields=['option', 'completed_at'])
    return choice


# ─── La clienta: "otra opción" ────────────────────────────────────────────────

def request_alternative(client, slot_pk, option_pk, request_token):
    """Pide reemplazar UNA opción de su plan aprobado. Devuelve `(job, created)`."""
    if client.is_trainer:
        raise services.PlanNotAllowed('Este usuario no tiene planes.')
    token = services._parse_token(request_token)
    existing = PlanGenerationJob.objects.filter(request_token=token).first()
    if existing is not None:
        if existing.client_id != client.pk:
            raise services.PlanError('El formulario expiró. Recarga la página e inténtalo de nuevo.')
        return existing, False
    plan, slot, option = _member_slot_option(client, slot_pk, option_pk)
    running = services.active_job(client, ALTERNATIVE)
    if running is not None:
        return running, False

    from . import jobs
    jobs.recover_stale_jobs()
    result = services.assess(client, KIND_NUTRITION, client, check_quota=False)
    if result.eligibility.missing:
        raise services.PlanRequestNotReady(result.eligibility)
    week_left, day_left = alternatives_left(client, slot)
    if week_left <= 0:
        raise services.QuotaExceeded('Ya pediste varias opciones nuevas para esta comida esta semana. '
                                     'Prueba con las otras opciones o escríbele a tu entrenador.')
    if day_left <= 0 or not services.global_quota_ok():
        raise services.QuotaExceeded('Hoy ya no se pueden pedir más opciones nuevas. Inténtalo mañana.')

    common = dict(
        kind=ALTERNATIVE, client=client, requested_by=client, requested_by_role=PlanGenerationJob.ROLE_MEMBER,
        request_token=token, health_profile=result.profile, slot=slot, replaces_option=option,
        max_attempts=settings.AI_PLANS_MAX_ATTEMPTS,
    )
    if result.eligibility.blocked:
        job = PlanGenerationJob.objects.create(
            status=PlanGenerationJob.STATUS_BLOCKED, flags=list(result.eligibility.red_codes),
            error_code='blocked', finished_at=timezone.now(), **common)
        transaction.on_commit(lambda: notifications.trainer_job_blocked(job))
        return job, True
    try:
        with transaction.atomic():
            job = PlanGenerationJob.objects.create(
                status=PlanGenerationJob.STATUS_QUEUED, flags=list(result.eligibility.yellow_codes), **common)
            PlanEvent.objects.create(plan=plan, actor=client, action=PlanEvent.ACTION_CLIENT_ALTERNATIVE,
                                     note=f'Pidió otra opción para {slot.name}.')
    except IntegrityError:
        running = services.active_job(client, ALTERNATIVE) or PlanGenerationJob.objects.filter(request_token=token).first()
        if running is None:
            raise
        return running, False
    logger.info('plan job queued job_id=%s kind=%s role=member', job.pk, ALTERNATIVE)
    transaction.on_commit(lambda: jobs.start_job(job.pk))
    return job, True


def create_alternative(job, option_data, *, plan, slot, replaced):
    """Cambia la opción rechazada por la nueva (mismo lugar). Dentro de una transacción."""
    replaced = MealOption.objects.select_for_update().get(pk=replaced.pk)
    if not replaced.is_active:
        return None
    replaced.is_active = False
    replaced.save(update_fields=['is_active'])
    new = _save_option(slot, replaced.position, option_data, origin=MealOption.ORIGIN_CLIENT_REGEN,
                       needs_review=True, replaces=replaced)
    return new


# ─── Intercambios y lista de compras ──────────────────────────────────────────

def exchange_rows(client, plan):
    """Equivalencias por grupo con los alimentos permitidos HOY a la clienta."""
    profile = health.current_profile(client)
    if profile is None:
        return []
    foods = rules_foods.filter_foods(services.food_items(), services.nutrition_profile(profile, plan.flags))
    return rules_foods.exchange_groups(foods.allowed)


def shopping_list(client, plan):
    """Suma de gramos por alimento para los próximos 7 días según lo elegido; donde no hay elección
    se supone que rota A-B-C. Devuelve `(filas, días_con_suposición)`."""
    dates = meal_dates()
    slots = plan_slots(plan, only_active=True)
    chosen = {(c.slot_id, c.date): c.option_id for c in MealChoice.objects.filter(
        client=client, date__in=dates, slot__nutrition_plan__plan=plan, option__is_active=True)}
    lines = {}
    assumed = 0
    for entry in slots:
        slot, options = entry['slot'], entry['options']
        if not options:
            continue
        by_id = {o['option'].id: o for o in options}
        for index, day in enumerate(dates):
            picked = by_id.get(chosen.get((slot.id, day)))
            if picked is None:
                picked = options[index % len(options)]
                assumed += 1
            for item in picked['items']:
                line = lines.setdefault(item['food'].id, {'food': item['food'], 'grams': 0})
                line['grams'] += item['grams']
    rows = sorted(lines.values(), key=lambda r: (rules_foods.GROUP_ORDER.get(r['food'].group, 99), r['food'].name))
    for row in rows:
        row['household'] = rules_foods.household_text(row['food'], row['grams'])
    return rows, assumed
