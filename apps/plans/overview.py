"""Planes y trabajos de UNA clienta para el entrenador: pantalla D-01 (docs/contracts/
plan-ia-fase-d.md, sección 5).

Solo CONSULTA y arma el contexto (la plantilla no calcula nada). Un número fijo de consultas: los
planes se leen en una sola consulta (con `select_related` y `Exists`) y se reparten por tipo en
Python; los trabajos, en otra.

Sin el consentimiento `health_data` vigente (`content_visible` falso) no salen textos de banderas:
solo estados, códigos técnicos y frases genéricas. Nada se escribe en logs.
"""
import re

from django.db.models import Exists, OuterRef
from django.urls import reverse

from apps.accounts.permissions import INACTIVE_CLIENT_MESSAGE
from apps.health import dates as health_dates
from apps.health import services as health

from . import ai, services, texts, wellness
from .models import KIND_EXERCISE, KIND_NUTRITION, Plan, PlanEvent, PlanGenerationJob
from .rules import eligibility as rules_eligibility
from .rules.progress import KIND_LABELS

VERSIONS_PER_KIND = 15
PLANS_READ_MAX = 200           # tope de lectura de planes de una clienta (en la práctica son pocos)
JOBS_SHOWN = 20
KINDS = (KIND_EXERCISE, KIND_NUTRITION)
JOB_KIND_LABELS = {**KIND_LABELS, PlanGenerationJob.KIND_MEAL_ALTERNATIVE: 'Otra opción de comida'}
GENERATE_LABELS = {KIND_EXERCISE: 'Generar borrador de rutina con IA',
                   KIND_NUTRITION: 'Generar borrador de alimentación con IA'}
CLONABLE = (Plan.STATUS_APPROVED, Plan.STATUS_SUPERSEDED, Plan.STATUS_ARCHIVED)
_SHORT_CODES = (rules_eligibility.D06_FEW_EXERCISES, rules_eligibility.D06_FEW_FOODS, wellness.OUT_OF_SCOPE)


# ─── Frases de los trabajos (sección 5.6) ─────────────────────────────────────

def failed_text(code):
    """Frase para el entrenador de un trabajo que falló. Código exacto primero, luego prefijos."""
    code = code or ''
    if code in texts.JOB_FAILED_EXACT:
        return texts.JOB_FAILED_EXACT[code]
    if code.startswith('not_ready:'):
        missing = code.split(':', 1)[1]
        text = texts.TRAINER_REQUIREMENT_TEXTS.get(missing, (missing + '.',))[0]
        return texts.JOB_FAILED_NOT_READY.format(text=text)
    if re.fullmatch(r'ai_http_5\d\d', code):
        return texts.JOB_TEXT_DOWN
    if re.fullmatch(r'ai_http_\d{3}', code):
        return texts.JOB_TEXT_CONFIG
    if code.startswith('ai_finish_'):
        return texts.JOB_TEXT_UNREADABLE
    if code.startswith('validation_failed:') or re.fullmatch(r'V\d\d_[A-Z_]+', code):
        return texts.JOB_TEXT_VALIDATION
    return texts.JOB_FAILED_GENERIC.format(code=code or 'desconocido')


def blocked_summary(flags):
    if rules_eligibility.D06_FEW_EXERCISES in flags:
        return 'Bloqueado por seguridad: no hay suficientes ejercicios compatibles con la ficha.'
    if rules_eligibility.D06_FEW_FOODS in flags:
        return 'Bloqueado por seguridad: no hay suficientes alimentos compatibles con la ficha.'
    if wellness.OUT_OF_SCOPE in flags:
        return 'Bloqueado: la alimentación clínica está pausada.'
    return ('Bloqueado por seguridad: la ficha requiere valoración profesional antes de un plan automático. '
            'No se llamó a la IA.')


def job_summary(job):
    """Frase ya redactada del estado de un trabajo (tabla de la sección 5.6)."""
    status = job.status
    if status == PlanGenerationJob.STATUS_QUEUED:
        return 'En cola: empezará en un momento.'
    if status == PlanGenerationJob.STATUS_RUNNING:
        if job.progress_total > 1:
            return f'Generando el borrador (paso {job.progress_done} de {job.progress_total}).'
        return 'Generando el borrador.'
    if status == PlanGenerationJob.STATUS_SUCCEEDED:
        if job.kind == PlanGenerationJob.KIND_MEAL_ALTERNATIVE:
            return 'Listo: se creó una opción nueva para la clienta.'
        if job.plan is not None:
            return f'Listo: se creó el borrador (versión {job.plan.version}).'
        return 'Listo: se creó el borrador.'
    if status == PlanGenerationJob.STATUS_BLOCKED:
        return blocked_summary(job.flags or [])
    return failed_text(job.error_code)


def job_rows(client, content_visible):
    jobs = list(PlanGenerationJob.objects.filter(client=client).select_related('plan')
                .order_by('-created_at', '-pk')[:JOBS_SHOWN + 1])
    truncated = len(jobs) > JOBS_SHOWN
    rows = []
    for job in jobs[:JOBS_SHOWN]:
        status = job.status
        tone = {PlanGenerationJob.STATUS_SUCCEEDED: 'good', PlanGenerationJob.STATUS_BLOCKED: 'warning',
                PlanGenerationJob.STATUS_FAILED: 'danger'}.get(status, 'info')
        flag_texts = []
        if status == PlanGenerationJob.STATUS_BLOCKED and content_visible:
            flag_texts = [row['text'] for row in services.describe_codes(job.flags)
                          if row['code'] not in _SHORT_CODES]
        rows.append({
            'job': job,
            'kind_label': JOB_KIND_LABELS.get(job.kind, job.kind),
            'status': status,
            'status_label': job.get_status_display(),
            'tone': tone,
            'summary': job_summary(job),
            'flag_texts': flag_texts,
            'technical_code': job.error_code or '',
            'requested_by_label': dict(PlanGenerationJob.ROLE_CHOICES).get(job.requested_by_role, ''),
            'plan_id': job.plan_id,
        })
    return rows, truncated


# ─── Planes por tipo (sección 5.2) ────────────────────────────────────────────

def _origin(plan, parent_version):
    if plan.was_cloned:
        label = f'Copia de la versión {parent_version}' if parent_version else 'Copia de otra versión'
        return 'clone', label
    if parent_version:
        return 'ai', f'Regenerado con IA (de la versión {parent_version})'
    return 'ai', 'Generado con IA'


def plan_groups(client, client_active, today):
    """Los 2 grupos (ejercicio y alimentación). Una consulta de planes y una de la ficha vigente (solo su id)."""
    plans = list(Plan.objects.filter(client=client).select_related('approved_by', 'parent').annotate(
        was_cloned=Exists(PlanEvent.objects.filter(plan=OuterRef('pk'), action=PlanEvent.ACTION_CLONED)),
    ).order_by('-version', '-pk')[:PLANS_READ_MAX + 1])
    profile = health.current_profile(client)
    profile_id = profile.pk if profile is not None else None
    by_kind = {kind: [] for kind in KINDS}
    for plan in plans[:PLANS_READ_MAX]:
        if plan.kind not in by_kind:
            continue
        parent_version = plan.parent.version if plan.parent_id and plan.parent is not None else None
        origin, origin_label = _origin(plan, parent_version)
        pending = plan.status in Plan.PENDING_STATUSES
        current = plan.status == Plan.STATUS_APPROVED
        by_kind[plan.kind].append({
            'plan': plan,
            'status_label': plan.get_status_display(),
            'is_current': current,
            'is_pending': pending,
            'origin': origin,
            'origin_label': origin_label,
            'parent_version': parent_version,
            'flags_count': len(plan.flags or []),
            'stale': (profile_id is None or plan.health_profile_id != profile_id) if (pending or current) else None,
            'expired': bool(current and plan.valid_until and plan.valid_until < today),
            'can_clone': plan.status in CLONABLE and client_active,
        })
    groups = []
    for kind in KINDS:
        rows = by_kind[kind]
        groups.append({
            'kind': kind,
            'label': KIND_LABELS[kind],
            'current': next((row for row in rows if row['is_current']), None),
            'draft': next((row for row in rows if row['is_pending']), None),
            'rows': rows[:VERSIONS_PER_KIND],
            'rows_truncated': len(rows) > VERSIONS_PER_KIND,
            'total': len(rows),
        })
    return groups


# ─── Botón «Generar borrador» (sección 5.3) ───────────────────────────────────

def _requirement_url(code, client):
    if code == rules_eligibility.D03_MEASURE:
        return reverse('trainer_measurement_add', args=[client.pk])
    if code == rules_eligibility.D04_INCOHERENT:
        return reverse('trainer_health_profile', args=[client.pk])
    if code == rules_eligibility.D07_NUTRITION_DETAILS:
        return reverse('trainer_health_profile_edit', args=[client.pk])
    return None


def blocked_text(red_codes):
    red_codes = tuple(red_codes)
    if wellness.OUT_OF_SCOPE in red_codes:
        return texts.TRAINER_BLOCKED_TEXTS[wellness.OUT_OF_SCOPE]
    for code in (rules_eligibility.D06_FEW_EXERCISES, rules_eligibility.D06_FEW_FOODS):
        if red_codes == (code,):
            return texts.TRAINER_BLOCKED_TEXTS[code]
    return texts.TRAINER_BLOCKED_GENERIC


def trainer_missing(codes, client):
    """Requisitos que faltan, en voz del entrenador: `[{code, text, action, url}]`."""
    rows = []
    for code in codes:
        text, action = texts.TRAINER_REQUIREMENT_TEXTS[code]
        rows.append({'code': code, 'text': text, 'action': action, 'url': _requirement_url(code, client)})
    return rows


def trainer_missing_text(codes):
    """Los requisitos que faltan en una sola frase (para el mensaje de `trainer_plan_generate`)."""
    return '; '.join(texts.TRAINER_REQUIREMENT_TEXTS[code][0] for code in codes)


def generate_info(client, kind, actor, *, client_active, content_visible, active_job, has_pending_draft):
    assessment = services.assess(client, kind, actor)
    eligibility = assessment.eligibility
    blocked = eligibility.blocked
    return {
        'label': GENERATE_LABELS[kind],
        'request_token': services.new_request_token(),
        'can_request': bool(eligibility.can_request and client_active and active_job is None),
        'blocked': blocked,
        'blocked_text': blocked_text(eligibility.red_codes) if blocked else '',
        'blocked_flags': ([row for row in services.describe_codes(eligibility.red_codes)
                           if row['code'] not in _SHORT_CODES] if (blocked and content_visible) else []),
        'missing': trainer_missing(eligibility.missing, client),
        'active_job': active_job,
        'has_pending_draft': has_pending_draft,
    }


# ─── Contexto completo de D-01 ────────────────────────────────────────────────

def client_plans_context(client, actor):
    """Claves del contexto de `trainer/client_plans.html` (sección 5.1)."""
    today = health_dates.today()
    client_active = bool(client.is_active)
    content_visible = health.trainer_can_see_content(client)
    groups = plan_groups(client, client_active, today)
    active = {job.kind: job for job in PlanGenerationJob.objects.filter(
        client=client, kind__in=KINDS, status__in=PlanGenerationJob.ACTIVE_STATUSES)}
    generate = {
        group['kind']: generate_info(
            client, group['kind'], actor, client_active=client_active, content_visible=content_visible,
            active_job=active.get(group['kind']), has_pending_draft=group['draft'] is not None)
        for group in groups
    }
    jobs, truncated = job_rows(client, content_visible)
    return {
        'client_active': client_active,
        'inactive_message': '' if client_active else INACTIVE_CLIENT_MESSAGE,
        'content_visible': content_visible,
        'ai_mode': ai.ai_mode(),
        'groups': groups,
        'generate': generate,
        'jobs': jobs,
        'jobs_truncated': truncated,
    }
