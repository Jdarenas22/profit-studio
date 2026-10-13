"""Planes con IA (HTTP). La lógica está en services.py y jobs.py.

Clienta: solo lo suyo (`request.user`, ninguna ruta recibe el id de otra persona salvo el de su
propio trabajo, filtrado por `client=request.user`), con membresía vigente y solo planes
`approved`. Entrenador: `clients_for_trainer` / `get_client_for_trainer` (lo no accesible = 404).
Todo responde `Cache-Control: no-store` y, apagada la función, 404.
"""
from functools import wraps
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import member_required, trainer_required
from apps.accounts.form_utils import errors_dict
from apps.accounts.permissions import clients_for_trainer, get_client_for_trainer
from apps.health import services as health
from apps.health.choices import EQUIPMENT_CHOICES, labels_for
from apps.health.models import PURPOSE_AI, PURPOSE_HEALTH_DATA, HealthAccessLog

from . import adaptation as adaptation_service
from . import ai, jobs, nutrition, services
from .forms import (
    AlternativeForm, ApproveForm, ArchiveForm, GenerateForm, MealChoiceForm, PlanRequestForm, RegenerateForm,
    ReviewOptionForm, parse_option_post,
)
from .models import KIND_EXERCISE, KIND_NUTRITION, MealOption, Plan, PlanGenerationJob
from .rules import eligibility as rules_eligibility
from .rules import foods as rules_foods
from .texts import DISCLAIMER

HTMX_STOP_POLLING = 286     # htmx deja de sondear con este código
ATTENTION_DAYS = 14


JOB_MESSAGES = {
    PlanGenerationJob.STATUS_QUEUED: 'Recibimos tu pedido. Empezaremos a armar el borrador en un momento.',
    PlanGenerationJob.STATUS_RUNNING: 'Estamos armando el borrador de tu plan. Esto puede tardar uno o dos minutos.',
    PlanGenerationJob.STATUS_SUCCEEDED: 'Listo: tu entrenador revisará el borrador y te avisaremos cuando esté aprobado.',
    PlanGenerationJob.STATUS_FAILED: 'No pudimos generar el borrador. Tu entrenador fue avisado y te ayudará.',
}


def plans_feature_required(view_func):
    """404 si los planes con IA están apagados (función de salud o `AI_PLANS_MODE=off`) y
    `Cache-Control: no-store` siempre."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not (health.health_features_enabled() and ai.ai_enabled()):
            raise Http404
        return view_func(request, *args, **kwargs)
    return never_cache(_wrapped)


def _requirement_url(code, next_url):
    query = f'?next={quote(next_url, safe="/")}'
    links = {
        rules_eligibility.D01_CONSENT_HEALTH: reverse('member_consent', args=[PURPOSE_HEALTH_DATA]) + query,
        rules_eligibility.D01_CONSENT_AI: reverse('member_consent', args=[PURPOSE_AI]) + query,
        rules_eligibility.D02_PROFILE: reverse('member_health_profile'),
        rules_eligibility.D02_PROFILE_CONFIRM: reverse('member_health_profile'),
        rules_eligibility.D03_MEASURE: reverse('member_measurement_add'),
        rules_eligibility.D04_INCOHERENT: reverse('member_health_profile') + '?edit=1',
    }
    return links.get(code)


def _eligibility_context(assessment, next_url):
    eligibility = assessment.eligibility
    return {
        'can_request': eligibility.can_request,
        'blocked': eligibility.blocked,
        'blocked_message': assessment.blocked_message if eligibility.blocked else '',
        'missing': [{'code': code, 'text': rules_eligibility.REQUIREMENT_TEXTS[code],
                     'url': _requirement_url(code, next_url)} for code in eligibility.missing],
    }


ALTERNATIVE_MESSAGES = {
    PlanGenerationJob.STATUS_QUEUED: 'Recibimos tu pedido. Armaremos otra opción en un momento.',
    PlanGenerationJob.STATUS_RUNNING: 'Estamos armando otra opción para tu comida. Tarda menos de un minuto.',
    PlanGenerationJob.STATUS_SUCCEEDED: 'Listo: ya tienes una opción nueva. Tu entrenador la revisará.',
    PlanGenerationJob.STATUS_FAILED: 'No pudimos armar otra opción. Tu entrenador fue avisado y te ayudará.',
}
_FEW_ITEMS_CODES = (rules_eligibility.D06_FEW_EXERCISES, rules_eligibility.D06_FEW_FOODS)


def _job_context(job):
    blocked = job.status == PlanGenerationJob.STATUS_BLOCKED
    is_alternative = job.kind == PlanGenerationJob.KIND_MEAL_ALTERNATIVE
    message = (ALTERNATIVE_MESSAGES if is_alternative else JOB_MESSAGES).get(job.status, '')
    if blocked:
        few = next((code for code in _FEW_ITEMS_CODES if job.flags == [code]), None)
        message = rules_eligibility.REQUIREMENT_TEXTS[few] if few else rules_eligibility.BLOCKED_MESSAGE
    return {
        'job': job, 'job_message': message, 'job_finished': job.is_finished, 'job_blocked': blocked,
        'job_is_alternative': is_alternative,
        'job_back_url': reverse('member_nutrition_plan') if is_alternative else reverse('member_plans'),
        'job_back_label': 'Volver a mi alimentación' if is_alternative else 'Volver a Mi plan',
    }


# ─── Clienta ──────────────────────────────────────────────────────────────────

@plans_feature_required
@member_required
@require_GET
def member_plans(request):
    """F-08: portada "Mi plan". Solo el aprobado tiene contenido; lo pendiente es solo un estado."""
    user = request.user
    exercise_plan = services.approved_plan(user, KIND_EXERCISE)
    nutrition_plan = nutrition.member_plan(user)
    pending = services.pending_plans(user)
    return render(request, 'plans/member_plans.html', {
        'exercise_plan': exercise_plan,
        'nutrition_plan': nutrition_plan,
        'pending': pending.filter(kind=KIND_EXERCISE).exists(),
        'pending_nutrition': pending.filter(kind=KIND_NUTRITION).exists(),
        'active_job': PlanGenerationJob.objects.filter(
            client=user, status__in=PlanGenerationJob.ACTIVE_STATUSES).first(),
        'valid_until': exercise_plan.valid_until if exercise_plan else None,
        'nutrition_valid_until': nutrition_plan.valid_until if nutrition_plan else None,
        'disclaimer': DISCLAIMER,
        'can_request_more': services.feature_available(),
    })


@plans_feature_required
@member_required
def member_plan_request(request):
    """F-06: requisitos + botón "Pedir mi plan" (POST crea el trabajo)."""
    if request.method not in ('GET', 'HEAD', 'POST'):
        return HttpResponse(status=405)
    user = request.user
    if health.is_minor(user):
        return render(request, 'health/minor_blocked.html', status=403)
    here = reverse('member_plan_request')
    kind = (request.POST if request.method == 'POST' else request.GET).get('kind')
    kind = kind if kind in (KIND_EXERCISE, KIND_NUTRITION) else KIND_EXERCISE
    running = services.active_job(user, kind)
    if running is not None:
        return redirect('member_plan_request_status', pk=running.pk)

    if request.method == 'POST':
        form = PlanRequestForm(request.POST)
        if form.is_valid():
            try:
                job, _created = services.request_plan(
                    user, form.cleaned_data['kind'], user, form.cleaned_data['request_token'])
            except services.PlanRequestNotReady:
                messages.error(request, 'Aún faltan algunos pasos antes de pedir tu plan.')
            except services.PlanError as exc:
                messages.error(request, str(exc))
            else:
                return redirect('member_plan_request_status', pk=job.pk)
        else:
            for text in errors_dict(form).values():
                messages.error(request, text)

    assessment = services.assess(user, kind, user)
    return render(request, 'plans/request.html', {
        'eligibility': _eligibility_context(assessment, f'{here}?kind={kind}'),
        'kind': kind,
        'kinds': [{'code': KIND_EXERCISE, 'label': 'Plan de ejercicio', 'icon': 'fa-dumbbell',
                   'url': here, 'selected': kind == KIND_EXERCISE},
                  {'code': KIND_NUTRITION, 'label': 'Plan de alimentación', 'icon': 'fa-utensils',
                   'url': f'{here}?kind={KIND_NUTRITION}', 'selected': kind == KIND_NUTRITION}],
        'request_token': services.new_request_token(),
        'disclaimer': DISCLAIMER,
        'is_simulated': ai.ai_mode() == ai.MODE_SIMULATED,
    })


def _member_job(request, pk):
    return get_object_or_404(PlanGenerationJob, pk=pk, client=request.user)


@plans_feature_required
@member_required
@require_GET
def member_plan_request_status(request, pk):
    """F-07: estado del pedido (sin contenido del plan)."""
    jobs.recover_stale_jobs()
    return render(request, 'plans/request_status.html', {
        **_job_context(_member_job(request, pk)), 'poll_url': reverse('member_plan_request_poll', args=[pk]),
    })


@plans_feature_required
@member_required
@require_GET
def member_plan_request_poll(request, pk):
    """Fragmento HTMX del estado. Responde 286 al terminar para detener el sondeo."""
    jobs.recover_stale_jobs()
    job = _member_job(request, pk)
    context = {**_job_context(job), 'poll_url': reverse('member_plan_request_poll', args=[pk])}
    response = render(request, 'plans/partials/job_status.html', context)
    if job.is_finished:
        response.status_code = HTMX_STOP_POLLING
    return response


# ─── Entrenador ───────────────────────────────────────────────────────────────

def _trainer_plan(request, pk):
    try:
        return services.get_plan_for_trainer(request.user, pk)
    except Plan.DoesNotExist:
        raise Http404 from None


@plans_feature_required
@trainer_required
@require_GET
def trainer_plan_inbox(request):
    """F-09: borradores y planes en revisión de SUS clientas + pedidos en curso o que necesitan
    atención (bloqueados o fallidos de los últimos días)."""
    status = request.GET.get('status', '')
    all_plans = services.plans_for_trainer(request.user)
    # Planes aprobados con una opción nueva pedida por la clienta: esperan revisión
    regenerated_ids = set(MealOption.objects.filter(
        slot__nutrition_plan__plan__in=all_plans, is_active=True, needs_review=True,
        slot__nutrition_plan__plan__status=Plan.STATUS_APPROVED,
    ).values_list('slot__nutrition_plan__plan_id', flat=True))
    if status in (Plan.STATUS_DRAFT_AI, Plan.STATUS_IN_REVIEW, Plan.STATUS_APPROVED):
        plans = all_plans.filter(status=status)
    else:
        status = ''
        plans = all_plans.filter(Q(status__in=Plan.PENDING_STATUSES) | Q(pk__in=regenerated_ids))
    rows = [{'plan': plan, 'flags': services.describe_codes(plan.flags),
             'client_regenerated': plan.pk in regenerated_ids}
            for plan in plans.order_by('-created_at')[:200]]
    accessible = clients_for_trainer(request.user)
    job_qs = PlanGenerationJob.objects.filter(client__in=accessible).select_related('client')
    attention = job_qs.filter(
        status__in=(PlanGenerationJob.STATUS_BLOCKED, PlanGenerationJob.STATUS_FAILED),
        created_at__gte=services._since(services.DAY * ATTENTION_DAYS)).order_by('-created_at')[:50]
    return render(request, 'trainer/plan_inbox.html', {
        'rows': rows,
        'status': status,
        'status_choices': [('', 'Por revisar'), (Plan.STATUS_DRAFT_AI, 'Borradores de la IA'),
                           (Plan.STATUS_IN_REVIEW, 'En revisión'), (Plan.STATUS_APPROVED, 'Aprobados')],
        'pending_count': all_plans.filter(Q(status__in=Plan.PENDING_STATUSES) | Q(pk__in=regenerated_ids)).count(),
        'active_jobs': job_qs.filter(status__in=PlanGenerationJob.ACTIVE_STATUSES).order_by('-created_at'),
        'attention_jobs': [{'job': job, 'flags': services.describe_codes(job.flags)} for job in attention],
        'ai_mode': ai.ai_mode(),
    })


def _profile_summary(request, client, profile):
    """Resumen de la ficha para decidir (solo con `health_data` vigente; queda registrado)."""
    if profile is None or not health.trainer_can_see_content(client):
        return None
    health.log_access(request.user, client, HealthAccessLog.ACTION_VIEW, profile.version)
    return {
        'version': profile.version,
        'age': profile.age,
        'goal': client.get_training_goal_display(),
        'experience': profile.get_experience_level_display(),
        'activity': profile.get_activity_level_display(),
        'days': profile.training_days_per_week,
        'minutes': profile.session_minutes,
        'place': profile.get_training_place_display(),
        'equipment': labels_for(EQUIPMENT_CHOICES, profile.equipment),
        'injuries': profile.injuries_labels,
        'conditions': profile.conditions_labels,
        'needs_confirmation': profile.needs_confirmation,
    }


def _plan_detail_context(request, plan, approve_errors=None, regenerate_errors=None, archive_errors=None):
    client = plan.client
    routine_days = []
    if plan.routine_id:
        routine_days = list(plan.routine.days.prefetch_related('exercises__exercise'))
    regen_job = PlanGenerationJob.objects.filter(
        parent_plan=plan, status__in=PlanGenerationJob.ACTIVE_STATUSES).first()
    is_nutrition = plan.kind == KIND_NUTRITION
    review = nutrition.review_context(plan) if is_nutrition and hasattr(plan, 'nutrition') else None
    problems = nutrition.approval_problems(plan) if is_nutrition and plan.is_pending else []
    can_validate = services.can_validate_nutrition(request.user)
    return {
        'is_nutrition': is_nutrition,
        'nutrition_review': review,
        'approval_problems': problems,
        'plan': plan,
        'client': client,
        'flags': services.yellow_flags(plan),
        'stale': services.is_stale(plan),
        'profile_summary': _profile_summary(request, client, health.current_profile(client)),
        'events': plan.events.select_related('actor')[:50],
        'routine_days': routine_days,
        'conflicts': services.routine_conflicts(plan) if plan.is_pending else [],
        'can_validate_nutrition': can_validate,
        'can_approve': (plan.is_pending and not (plan.is_simulated and not settings.DEBUG) and not problems
                        and (can_validate or not is_nutrition)),
        'simulated_blocked': plan.is_simulated and not settings.DEBUG,
        'regen_job': regen_job,
        'request_token': services.new_request_token(),
        'approve_errors': approve_errors or {},
        'regenerate_errors': regenerate_errors or {},
        'archive_errors': archive_errors or {},
        'form': request.POST if request.method == 'POST' else {},
        'disclaimer': DISCLAIMER,
        'adaptation': adaptation_service.panel_for_plan(plan),
    }


@plans_feature_required
@trainer_required
@require_GET
def trainer_plan_detail(request, pk):
    """F-10: revisión del borrador (resumen de la rutina, advertencias, ficha, historial)."""
    plan = _trainer_plan(request, pk)
    return render(request, 'trainer/plan_detail.html', _plan_detail_context(request, plan))


@plans_feature_required
@trainer_required
@require_POST
def trainer_plan_take(request, pk):
    plan = _trainer_plan(request, pk)
    if services.take_for_review(plan, request.user):
        messages.success(request, 'Tomaste el plan para revisión.')
    if request.POST.get('then') == 'builder' and plan.routine_id:
        return redirect('trainer_routine_builder', pk=plan.routine_id)
    return redirect('trainer_plan_detail', pk=plan.pk)


@plans_feature_required
@trainer_required
@require_POST
def trainer_plan_approve(request, pk):
    plan = _trainer_plan(request, pk)
    form = ApproveForm(request.POST)
    if not form.is_valid():
        errors = errors_dict(form)
        messages.error(request, 'Revisa el formulario de aprobación.')
        return render(request, 'trainer/plan_detail.html',
                      _plan_detail_context(request, plan, approve_errors=errors), status=400)
    try:
        services.approve_plan(plan, request.user, note_for_client=form.cleaned_data['note_for_client'],
                              warnings_ack=form.cleaned_data['warnings_ack'],
                              stale_ack=form.cleaned_data['stale_ack'])
    except services.PlanError as exc:
        messages.error(request, str(exc))
        return render(request, 'trainer/plan_detail.html',
                      _plan_detail_context(request, plan, approve_errors={'general': str(exc)}), status=400)
    messages.success(request, 'Plan aprobado. La clienta ya puede verlo.')
    return redirect('trainer_plan_detail', pk=plan.pk)


@plans_feature_required
@trainer_required
@require_POST
def trainer_plan_regenerate(request, pk):
    plan = _trainer_plan(request, pk)
    form = RegenerateForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Revisa las notas para regenerar.')
        return render(request, 'trainer/plan_detail.html',
                      _plan_detail_context(request, plan, regenerate_errors=errors_dict(form)), status=400)
    try:
        job, _created = services.regenerate_plan(plan, request.user, form.cleaned_data['notes'],
                                                 form.cleaned_data['request_token'])
    except services.PlanRequestNotReady as exc:
        texts = '; '.join(exc.eligibility.missing_texts)
        messages.error(request, f'No se puede regenerar todavía: {texts}')
        return redirect('trainer_plan_detail', pk=plan.pk)
    except services.PlanError as exc:
        messages.error(request, str(exc))
        return redirect('trainer_plan_detail', pk=plan.pk)
    return redirect('trainer_plan_job', pk=job.pk)


@plans_feature_required
@trainer_required
@require_POST
def trainer_plan_archive(request, pk):
    plan = _trainer_plan(request, pk)
    form = ArchiveForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Revisa el motivo.')
        return render(request, 'trainer/plan_detail.html',
                      _plan_detail_context(request, plan, archive_errors=errors_dict(form)), status=400)
    try:
        services.archive_plan(plan, request.user, form.cleaned_data['reason'])
    except services.PlanError as exc:
        messages.error(request, str(exc))
        return redirect('trainer_plan_detail', pk=plan.pk)
    messages.success(request, 'Plan archivado.')
    return redirect('trainer_plan_inbox')


@plans_feature_required
@trainer_required
@require_POST
def trainer_plan_generate(request, client_pk):
    """El entrenador pide un borrador de ejercicio para una de SUS clientas."""
    client = get_client_for_trainer(request, client_pk)
    back = redirect('trainer_client_detail', pk=client.pk)
    form = GenerateForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'El formulario expiró. Recarga la página.')
        return back
    try:
        job, _created = services.request_plan(client, form.cleaned_data['kind'], request.user,
                                              form.cleaned_data['request_token'])
    except services.PlanRequestNotReady as exc:
        messages.error(request, 'Aún no se puede generar: ' + '; '.join(exc.eligibility.missing_texts))
        return back
    except services.PlanError as exc:
        messages.error(request, str(exc))
        return back
    return redirect('trainer_plan_job', pk=job.pk)


def _trainer_job(request, pk):
    return get_object_or_404(
        PlanGenerationJob.objects.select_related('client', 'plan'),
        pk=pk, client__in=clients_for_trainer(request.user))


@plans_feature_required
@trainer_required
@require_GET
def trainer_plan_job(request, pk):
    jobs.recover_stale_jobs()
    job = _trainer_job(request, pk)
    return render(request, 'trainer/plan_job.html', {
        **_job_context(job), 'flags': services.describe_codes(job.flags),
        'poll_url': reverse('trainer_plan_job_poll', args=[pk]),
    })


@plans_feature_required
@trainer_required
@require_GET
def trainer_plan_job_poll(request, pk):
    jobs.recover_stale_jobs()
    job = _trainer_job(request, pk)
    response = render(request, 'plans/partials/job_status.html', {
        **_job_context(job), 'trainer_mode': True, 'flags': services.describe_codes(job.flags),
        'poll_url': reverse('trainer_plan_job_poll', args=[pk]),
    })
    if job.is_finished:
        response.status_code = HTMX_STOP_POLLING
    return response


# ─── Clienta: plan de alimentación (fase C) ───────────────────────────────────

def _nutrition_plan_or_none(request):
    return nutrition.member_plan(request.user)


def _date_pills(selected):
    today = nutrition.meal_dates()[0]
    pills = []
    for day in nutrition.meal_dates():
        label = 'Hoy' if day == today else ('Mañana' if (day - today).days == 1 else '')
        pills.append({'date': day, 'iso': day.isoformat(), 'label': label, 'selected': day == selected})
    return pills


@plans_feature_required
@member_required
@require_GET
def member_nutrition_plan(request):
    """F-11/F-12: mi alimentación. Solo el plan aprobado; 3 opciones por comida, elegir una y pedir
    otra opción."""
    user = request.user
    plan = _nutrition_plan_or_none(request)
    if plan is None:
        messages.info(request, 'Aún no tienes un plan de alimentación aprobado.')
        return redirect('member_plans')
    on_date = nutrition.parse_meal_date(request.GET.get('date'))
    view = nutrition.day_view(user, plan, on_date)
    return render(request, 'plans/member_nutrition.html', {
        **view, 'plan': plan, 'nutrition': plan.nutrition, 'date': on_date, 'date_pills': _date_pills(on_date),
        'active_job': services.active_job(user, nutrition.ALTERNATIVE),
        'request_token': services.new_request_token(), 'disclaimer': DISCLAIMER,
    })


def _back_to_day(on_date, slot_id=None):
    url = reverse('member_nutrition_plan') + f'?date={on_date.isoformat()}'
    return redirect(f'{url}#slot-{slot_id}' if slot_id else url)


@plans_feature_required
@member_required
@require_POST
def member_meal_choice(request):
    form = MealChoiceForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'No se pudo guardar tu elección. Inténtalo de nuevo.')
        return redirect('member_nutrition_plan')
    data = form.cleaned_data
    try:
        nutrition.choose_option(request.user, data['slot'], data['option'], data['date'])
    except nutrition.OptionNotFound:
        raise Http404 from None
    except services.PlanError as exc:
        messages.error(request, str(exc))
        return redirect('member_nutrition_plan')
    return _back_to_day(data['date'], data['slot'])


@plans_feature_required
@member_required
@require_POST
def member_meal_alternative(request, slot_pk):
    """F-12: "otra opción" para una comida (cambia UNA de las 3 opciones)."""
    form = AlternativeForm(request.POST)
    if not form.is_valid():
        for text in errors_dict(form).values():
            messages.error(request, text)
        return redirect('member_nutrition_plan')
    try:
        job, _created = nutrition.request_alternative(
            request.user, slot_pk, form.cleaned_data['option'], form.cleaned_data['request_token'])
    except nutrition.OptionNotFound:
        raise Http404 from None
    except services.PlanRequestNotReady:
        messages.error(request, 'Aún faltan algunos pasos (autorizaciones o ficha) para pedir otra opción.')
        return redirect('member_nutrition_plan')
    except services.PlanError as exc:
        messages.error(request, str(exc))
        return redirect('member_nutrition_plan')
    return redirect('member_plan_request_status', pk=job.pk)


@plans_feature_required
@member_required
@require_GET
def member_exchanges(request):
    """F-13: equivalencias por porción (las calcula Python desde la tabla de alimentos)."""
    plan = _nutrition_plan_or_none(request)
    if plan is None:
        return redirect('member_plans')
    return render(request, 'plans/member_exchanges.html', {
        'plan': plan, 'groups': nutrition.exchange_rows(request.user, plan), 'disclaimer': DISCLAIMER})


@plans_feature_required
@member_required
@require_GET
def member_shopping_list(request):
    """F-13: lista de compras de los próximos 7 días."""
    plan = _nutrition_plan_or_none(request)
    if plan is None:
        return redirect('member_plans')
    rows, assumed = nutrition.shopping_list(request.user, plan)
    dates = nutrition.meal_dates()
    return render(request, 'plans/member_shopping_list.html', {
        'plan': plan, 'rows': rows, 'assumed': assumed, 'first_day': dates[0], 'last_day': dates[-1],
        'disclaimer': DISCLAIMER})


# ─── Entrenador: opciones de comida (fase C) ──────────────────────────────────

def _trainer_option(request, pk, opt_pk):
    plan = _trainer_plan(request, pk)
    option = get_object_or_404(
        MealOption.objects.select_related('slot__nutrition_plan'), pk=opt_pk, slot__nutrition_plan__plan=plan)
    return plan, option


def _food_groups(foods):
    groups = {}
    for food in foods:
        groups.setdefault(food.group, []).append(food)
    return [{'label': rules_foods.GROUP_LABELS[code], 'foods': groups[code]}
            for code, _label in rules_foods.GROUPS if code in groups]


def _option_form_context(request, plan, option, foods, rows, name, preparation, errors, error_list=()):
    return {
        'plan': plan, 'option': option, 'slot': option.slot, 'client': plan.client,
        'food_groups': _food_groups(foods), 'rows': rows, 'name': name, 'preparation': preparation,
        'errors': errors, 'error_list': list(error_list),
    }


@plans_feature_required
@trainer_required
def trainer_meal_option_edit(request, pk, opt_pk):
    """F-14: el entrenador cambia alimentos y gramos de una opción; el backend recalcula kcal y
    macros y revalida contra la ficha."""
    if request.method not in ('GET', 'HEAD', 'POST'):
        return HttpResponse(status=405)
    plan, option = _trainer_option(request, pk, opt_pk)
    assessment = services.assess(plan.client, KIND_NUTRITION, request.user, check_quota=False)
    if assessment.foods is None:
        messages.error(request, 'No se puede editar ahora: faltan requisitos de la clienta o su ficha requiere '
                                'valoración profesional.')
        return redirect('trainer_plan_detail', pk=plan.pk)
    foods = assessment.foods.allowed
    if request.method == 'POST':
        name, preparation, items, errors = parse_option_post(request.POST)
        error_list = []
        if not errors:
            try:
                _option, result = nutrition.edit_option(plan, option, request.user, name, preparation, items)
            except nutrition.OptionInvalid as exc:
                error_list = [detail for _code, detail in exc.errors]
            except services.PlanError as exc:
                messages.error(request, str(exc))
                return redirect('trainer_plan_detail', pk=plan.pk)
            else:
                if result.out_of_band:
                    messages.warning(request, 'Guardado. Ojo: la energía de la opción quedó fuera de ±5 % del '
                                              'objetivo de la comida.')
                elif result.low_protein:
                    messages.warning(request, 'Guardado. Ojo: la proteína de la opción quedó por debajo del 90 % '
                                              'del objetivo de la comida.')
                else:
                    messages.success(request, 'Opción guardada.')
                return redirect('trainer_plan_detail', pk=plan.pk)
        rows = [{'food_id': i['food_id'], 'grams': i['grams']} for i in items] or [{'food_id': '', 'grams': ''}]
        return render(request, 'trainer/meal_option_form.html', _option_form_context(
            request, plan, option, foods, rows, name, preparation, errors, error_list), status=400)
    rows = [{'food_id': item.food_id, 'grams': item.grams} for item in option.items.all()]
    return render(request, 'trainer/meal_option_form.html', _option_form_context(
        request, plan, option, foods, rows, option.name, option.preparation, {}))


@plans_feature_required
@trainer_required
@require_POST
def trainer_meal_option_review(request, pk, opt_pk):
    plan, option = _trainer_option(request, pk, opt_pk)
    form = ReviewOptionForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Elige qué hacer con la opción.')
        return redirect('trainer_plan_detail', pk=plan.pk)
    try:
        nutrition.review_option(plan, option, request.user, form.cleaned_data['action'])
    except services.PlanError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'Listo.')
    return redirect('trainer_plan_detail', pk=plan.pk)
