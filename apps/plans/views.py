"""Planes con IA (HTTP). La lógica está en services.py y jobs.py.

Clienta: solo lo suyo (`request.user`, ninguna ruta recibe el id de otra persona salvo el de su
propio trabajo, filtrado por `client=request.user`), con membresía vigente y solo planes
`approved`. Entrenador: `clients_for_trainer` / `get_client_for_trainer` (lo no accesible = 404).
Todo responde `Cache-Control: no-store` y, apagada la función, 404.
"""
from functools import wraps

from django.conf import settings
from django.contrib import messages
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

from . import ai, jobs, services
from .forms import ApproveForm, ArchiveForm, GenerateForm, PlanRequestForm, RegenerateForm
from .models import KIND_EXERCISE, Plan, PlanGenerationJob
from .rules import eligibility as rules_eligibility
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
    query = f'?next={next_url}'
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


def _job_context(job):
    blocked = job.status == PlanGenerationJob.STATUS_BLOCKED
    message = JOB_MESSAGES.get(job.status, '')
    if blocked:
        message = (rules_eligibility.REQUIREMENT_TEXTS[rules_eligibility.D06_FEW_EXERCISES]
                   if job.flags == [rules_eligibility.D06_FEW_EXERCISES] else rules_eligibility.BLOCKED_MESSAGE)
    return {'job': job, 'job_message': message, 'job_finished': job.is_finished, 'job_blocked': blocked}


# ─── Clienta ──────────────────────────────────────────────────────────────────

@plans_feature_required
@member_required
@require_GET
def member_plans(request):
    """F-08: portada "Mi plan". Solo el aprobado tiene contenido; lo pendiente es solo un estado."""
    user = request.user
    exercise_plan = services.approved_plan(user, KIND_EXERCISE)
    return render(request, 'plans/member_plans.html', {
        'exercise_plan': exercise_plan,
        'nutrition_plan': None,             # fase C
        'pending': services.pending_plans(user).exists(),
        'active_job': PlanGenerationJob.objects.filter(
            client=user, status__in=PlanGenerationJob.ACTIVE_STATUSES).first(),
        'valid_until': exercise_plan.valid_until if exercise_plan else None,
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
    running = services.active_job(user, KIND_EXERCISE)
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

    assessment = services.assess(user, KIND_EXERCISE, user)
    return render(request, 'plans/request.html', {
        'eligibility': _eligibility_context(assessment, here),
        'kinds': [{'code': KIND_EXERCISE, 'label': 'Plan de ejercicio'}],
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
    plans = services.plans_for_trainer(request.user)
    if status in (Plan.STATUS_DRAFT_AI, Plan.STATUS_IN_REVIEW, Plan.STATUS_APPROVED):
        plans = plans.filter(status=status)
    else:
        status = ''
        plans = plans.filter(status__in=Plan.PENDING_STATUSES)
    rows = [{'plan': plan, 'flags': services.describe_codes(plan.flags),
             'client_regenerated': False}   # "otra opción" de la clienta llega en la fase C
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
        'pending_count': services.plans_for_trainer(request.user).filter(status__in=Plan.PENDING_STATUSES).count(),
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
    return {
        'plan': plan,
        'client': client,
        'flags': services.yellow_flags(plan),
        'stale': services.is_stale(plan),
        'profile_summary': _profile_summary(request, client, health.current_profile(client)),
        'events': plan.events.select_related('actor')[:50],
        'routine_days': routine_days,
        'conflicts': services.routine_conflicts(plan) if plan.is_pending else [],
        'can_validate_nutrition': services.can_validate_nutrition(request.user),
        'can_approve': plan.is_pending and not (plan.is_simulated and not settings.DEBUG),
        'simulated_blocked': plan.is_simulated and not settings.DEBUG,
        'regen_job': regen_job,
        'request_token': services.new_request_token(),
        'approve_errors': approve_errors or {},
        'regenerate_errors': regenerate_errors or {},
        'archive_errors': archive_errors or {},
        'form': request.POST if request.method == 'POST' else {},
        'disclaimer': DISCLAIMER,
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
        job, _created = services.request_plan(client, KIND_EXERCISE, request.user,
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
