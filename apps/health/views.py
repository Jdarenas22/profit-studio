"""Consentimiento y ficha de salud (HTTP). La lógica está en services.py.

Rutas de la clienta: solo ella con membresía vigente (el entrenador recibe 403), siempre sobre
`request.user` (ninguna recibe el id de otra persona) y con `HEALTH_FEATURES_ENABLED` encendido
(apagado = 404). Rutas del entrenador: `get_client_for_trainer` (lo no accesible = 404).
Todas las respuestas con datos de salud llevan `Cache-Control: no-store`.
"""
import json
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.serializers.json import DjangoJSONEncoder
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import member_required, trainer_required
from apps.accounts.form_utils import errors_dict, flash_errors
from apps.accounts.models import User
from apps.accounts.permissions import get_client_for_trainer
from apps.assessments.services import latest_anthropometrics, latest_height
from apps.plans.rules.flags import add_months

from . import choices as ch
from . import dates, services
from .decorators import health_feature_required
from .forms import (
    HealthProfileForm, empty_form_values, error_steps, first_error_step,
    form_values_from_post, form_values_from_profile,
)
from .models import PURPOSE_AI, PURPOSE_CHOICES, PURPOSE_HEALTH_DATA, HealthAccessLog

PURPOSE_LABELS = dict(PURPOSE_CHOICES)
DEFAULT_NEXT = 'member_measurement_list'


def _check_purpose(purpose):
    if not services.is_valid_purpose(purpose):
        raise Http404


def _safe_next(request):
    """Destino al terminar (`next`), solo si es una ruta interna; si no, el historial de medidas."""
    target = request.POST.get('next') or request.GET.get('next') or ''
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return target
    return None


def _consent_context(request, purpose, text, record, errors=None, form=None):
    return {
        'purpose': purpose,
        'purpose_label': PURPOSE_LABELS[purpose],
        'text_version': text.version if text else None,
        'body': text.body if text else '',
        'already_granted': record is not None,
        'granted_at': record.granted_at if record else None,
        'text_available': text is not None,
        'next': _safe_next(request) or '',
        'privacy_policy_url': getattr(settings, 'PRIVACY_POLICY_URL', ''),
        'errors': errors or {},
        'form': form or {},
    }


@health_feature_required
@member_required
def member_consent(request, purpose):
    _check_purpose(purpose)
    if services.is_minor(request.user):
        return render(request, 'health/minor_blocked.html', status=403)

    text = services.current_text(purpose)
    record = services.active_consent(request.user, purpose)

    if request.method == 'POST':
        errors = {}
        if text is None:
            errors['general'] = 'Este consentimiento aún no está disponible.'
        elif request.POST.get('accept') != 'on':
            errors['accept'] = 'Debes marcar la casilla para continuar.'
        else:
            try:
                accepted_version = int(request.POST.get('text_version', ''))
            except (TypeError, ValueError):
                accepted_version = None
            try:
                services.grant_consent(request.user, purpose, accepted_version)
            except services.ConsentError as exc:
                errors['text_version' if isinstance(exc, services.ConsentTextChanged) else 'general'] = str(exc)
            else:
                messages.success(request, 'Tu autorización quedó registrada.')
                return redirect(_safe_next(request) or DEFAULT_NEXT)
        # La plantilla solo pinta `errors.accept` junto a la casilla; el resto se avisa arriba
        for key, text_error in errors.items():
            if key != 'accept':
                messages.error(request, text_error)
        return render(request, 'health/consent.html',
                      _consent_context(request, purpose, text, record, errors, request.POST))

    return render(request, 'health/consent.html', _consent_context(request, purpose, text, record))


@health_feature_required
@member_required
@require_POST
def member_consent_revoke(request, purpose):
    _check_purpose(purpose)
    if services.revoke_consent(request.user, purpose):
        messages.success(request, 'Retiraste tu autorización. Ya no se usarán tus datos para esta finalidad.')
        # Al retirar la autorización de datos de salud se ofrece borrar la ficha guardada
        if purpose == PURPOSE_HEALTH_DATA and services.has_profile(request.user):
            return redirect(f"{reverse('member_health_delete')}?{urlencode({'from_revoke': 1})}")
    else:
        messages.info(request, 'No tenías una autorización activa para esta finalidad.')
    return redirect(_safe_next(request) or DEFAULT_NEXT)


# ─── Ficha de salud y alimentación — contexto común ───────────────────────────

def _choices_context():
    """Listas cerradas `[(código, etiqueta)]` para pintar casillas y selectores."""
    return {
        'sex': ch.SEX_CHOICES,
        'goals': User.GOAL_CHOICES,
        'conditions': ch.CONDITION_CHOICES,
        'medications': ch.MEDICATION_CHOICES,
        'injuries': ch.INJURY_CHOICES,
        'surgery': ch.SURGERY_CHOICES,
        'pregnancy': ch.PREGNANCY_CHOICES,
        'eating_disorder': ch.EATING_DISORDER_CHOICES,
        'diet': ch.DIET_CHOICES,
        'allergies': ch.ALLERGY_CHOICES,
        'allergy_severity': ch.ALLERGY_SEVERITY_CHOICES,
        'intolerances': ch.INTOLERANCE_CHOICES,
        'meal_slots': ch.MEAL_SLOT_CHOICES,
        'cooking': ch.COOKING_CHOICES,
        'budget': ch.BUDGET_CHOICES,
        'training_place': ch.TRAINING_PLACE_CHOICES,
        'equipment': ch.EQUIPMENT_CHOICES,
        'experience': ch.EXPERIENCE_CHOICES,
        'activity': ch.ACTIVITY_CHOICES,
    }


def _form_context(*, mode, client, profile, values, errors, last_height):
    """Contexto de las plantillas del formulario (F-04 y la del entrenador): mismo diccionario."""
    parq_rows = [
        {'key': key, 'number': number, 'text': text, 'field': f'parq_{key}', 'value': values.get(f'parq_{key}', '')}
        for number, (key, text) in enumerate(ch.PARQ_QUESTIONS, start=1)
    ]
    selected_slots = values.get('meal_slots', [])
    meal_rows = [
        {'code': code, 'label': label, 'required': code in ch.REQUIRED_MEAL_SLOTS,
         'checked': code in selected_slots, 'field': f'meal_time_{code}',
         'time': values.get(f'meal_time_{code}', '')}
        for code, label in ch.MEAL_SLOT_CHOICES
    ]
    today = dates.today()
    return {
        'mode': mode,                          # 'member' | 'trainer'
        'client': client,                      # solo en modo 'trainer'
        'profile': profile,                    # versión vigente o None
        'is_edit': profile is not None,
        'steps': ch.WIZARD_STEPS,
        'choices': _choices_context(),
        'required_meal_slots': list(ch.REQUIRED_MEAL_SLOTS),
        'meal_rows': meal_rows,
        'parq_rows': parq_rows,
        'errors': errors,
        'error_steps': error_steps(errors),
        'first_error_step': first_error_step(errors),
        'form': values,
        'last_height': last_height,
        'birth_date_min': add_months(today, -12 * services.MAX_AGE).isoformat(),
        'birth_date_max': add_months(today, -12 * services.ADULT_AGE).isoformat(),
        'today': today.isoformat(),
    }


def _validate_posted_profile(request, known_height):
    """Valida el POST. Devuelve (form, errors, values); `errors` vacío si todo está bien."""
    form = HealthProfileForm(request.POST, known_height=known_height)
    values = form_values_from_post(request.POST)
    if form.is_valid():
        return form, {}, values
    errors = errors_dict(form)
    flash_errors(request, form, errors, skip=tuple(form.fields))   # solo los generales (`__all__`)
    messages.error(request, 'Revisa los datos marcados: hay respuestas por corregir.')
    return form, errors, values


def _consent_redirect(next_url):
    return redirect(f"{reverse('member_consent', args=[PURPOSE_HEALTH_DATA])}?{urlencode({'next': next_url})}")


# ─── Ficha de salud — la clienta ──────────────────────────────────────────────

@health_feature_required
@member_required
def member_health_profile(request):
    """GET: resumen de la ficha (o el formulario si aún no tiene, o con `?edit=1`). POST: guarda
    una versión nueva. Menores de 18: 403 con `health/minor_blocked.html`."""
    user = request.user
    if request.method not in ('GET', 'HEAD', 'POST'):
        return HttpResponse(status=405)
    if services.is_minor(user):
        return render(request, 'health/minor_blocked.html', status=403)
    profile = services.current_profile(user)
    here = reverse('member_health_profile')

    if request.method == 'POST':
        if not services.has_health_consent(user):
            messages.error(request, 'Para guardar tu ficha necesitamos tu autorización vigente.')
            return _consent_redirect(here)
        known_height = latest_height(user)
        form, errors, values = _validate_posted_profile(request, known_height)
        if not errors:
            try:
                services.save_profile(user, form.cleaned_data, actor=user)
            except services.ProfileConsentRequired:
                messages.error(request, 'Para guardar tu ficha necesitamos tu autorización vigente.')
                return _consent_redirect(here)
            except services.ProfileError as exc:
                errors = {'general': str(exc)}
                messages.error(request, str(exc))
            else:
                messages.success(request, 'Tu ficha quedó guardada.')
                return redirect('member_health_profile')
        return render(request, 'health/profile_form.html', _form_context(
            mode='member', client=None, profile=profile, values=values, errors=errors,
            last_height=known_height))

    if profile is not None and request.GET.get('edit') != '1':
        return render(request, 'health/profile_detail.html', {
            'profile': profile,
            'needs_confirmation': profile.needs_confirmation,
            'edited_by_trainer': profile.created_by_role == profile.ROLE_TRAINER,
            'age': profile.age,
            'goal_label': user.get_training_goal_display(),
            'health_consent_valid': services.has_health_consent(user),
            'consent_cards': services.consent_cards(user),
            'versions': services.profile_versions(user),
        })

    if not services.has_health_consent(user):
        return _consent_redirect(f'{here}?edit=1' if profile is not None else here)
    values = (form_values_from_profile(profile, user.training_goal) if profile is not None
              else {**empty_form_values(), 'training_goal': user.training_goal})
    return render(request, 'health/profile_form.html', _form_context(
        mode='member', client=None, profile=profile, values=values, errors={},
        last_height=latest_height(user)))


@health_feature_required
@member_required
@require_POST
def member_health_profile_confirm(request):
    """La clienta confirma la versión vigente (la corrigió su entrenador)."""
    try:
        _, changed = services.confirm_profile(request.user)
    except services.ProfileConsentRequired:
        messages.error(request, 'Para confirmar tu ficha necesitamos tu autorización vigente.')
        return _consent_redirect(reverse('member_health_profile'))
    except services.ProfileNotFound:
        messages.error(request, 'Aún no tienes una ficha para confirmar.')
        return redirect('member_health_profile')
    messages.success(request, 'Confirmaste tus datos.' if changed else 'Tu ficha ya estaba confirmada.')
    return redirect('member_health_profile')


@health_feature_required
@member_required
@require_GET
def member_health_export(request):
    """Descarga un JSON con los datos de salud de la clienta (derecho de acceso). Deja registro."""
    payload = services.export_data(request.user)
    response = HttpResponse(
        json.dumps(payload, cls=DjangoJSONEncoder, ensure_ascii=False, indent=2),
        content_type='application/json; charset=utf-8',
    )
    response['Content-Disposition'] = 'attachment; filename="mis-datos-de-salud.json"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@health_feature_required
@member_required
def member_health_delete(request):
    """GET: página de confirmación. POST: borra TODAS las versiones de la ficha de la clienta."""
    if request.method not in ('GET', 'HEAD', 'POST'):
        return HttpResponse(status=405)
    user = request.user
    from_revoke = (request.POST.get('from_revoke') or request.GET.get('from_revoke')) == '1'
    if request.method == 'POST':
        count = services.delete_profile(user)
        if count:
            messages.success(request, 'Borramos tu ficha de salud y todas sus versiones.')
        else:
            messages.info(request, 'No tenías una ficha guardada.')
        return redirect('member_measurement_list')
    return render(request, 'health/profile_delete.html', {
        'profile': services.current_profile(user),
        'has_profile': services.has_profile(user),
        'versions_count': len(services.profile_versions(user)),
        'from_revoke': from_revoke,
    })


# ─── Ficha de salud — el entrenador ───────────────────────────────────────────

@never_cache
@trainer_required
@require_GET
def trainer_health_profile(request, client_pk):
    """Ficha completa de solo lectura + banderas. El contenido solo se muestra con el
    consentimiento `health_data` vigente de la clienta; cada vez que se muestra queda en
    `HealthAccessLog`."""
    client = get_client_for_trainer(request, client_pk)
    profile = services.current_profile(client)
    consent_ok = services.trainer_can_see_content(client)
    visible = profile is not None and consent_ok
    if visible:
        services.log_access(request.user, client, HealthAccessLog.ACTION_VIEW, profile.version)
    flags = services.profile_flags(client, profile) if (consent_ok or profile is None) else None
    return render(request, 'trainer/health_profile.html', {
        'client': client,
        'has_profile': profile is not None,
        'profile': profile if visible else None,
        'content_visible': visible,
        'consent_valid': consent_ok,
        'consent_health': services.active_consent(client, PURPOSE_HEALTH_DATA),
        'consent_ai': services.active_consent(client, PURPOSE_AI),
        'needs_confirmation': bool(visible and profile.needs_confirmation),
        'flags': flags,
        'is_minor': services.is_minor(client),
        'goal_label': client.get_training_goal_display(),
        'anthropometrics': latest_anthropometrics(client),
        'versions': services.profile_versions(client) if visible else [],
        'can_edit': visible and services.health_features_enabled(),
        'feature_enabled': services.health_features_enabled(),
    })


@health_feature_required
@trainer_required
def trainer_health_profile_edit(request, client_pk):
    """El entrenador corrige una ficha EXISTENTE (no la crea): crea una versión nueva sin
    confirmar que la clienta debe confirmar."""
    if request.method not in ('GET', 'HEAD', 'POST'):
        return HttpResponse(status=405)
    client = get_client_for_trainer(request, client_pk)
    back = redirect('trainer_health_profile', client_pk=client.pk)
    profile = services.current_profile(client)
    if profile is None:
        messages.error(request, 'La clienta aún no ha llenado su ficha: solo puedes corregir una ficha existente.')
        return back
    if not services.trainer_can_see_content(client):
        messages.error(request, 'La clienta no tiene una autorización vigente para tratar sus datos de salud.')
        return back

    known_height = latest_height(client)
    if request.method == 'POST':
        form, errors, values = _validate_posted_profile(request, known_height)
        if not errors:
            try:
                services.save_profile(client, form.cleaned_data, actor=request.user)
            except services.ProfileError as exc:
                errors = {'general': str(exc)}
                messages.error(request, str(exc))
            else:
                messages.success(request, 'Corrección guardada. La clienta deberá confirmar los cambios.')
                return back
    else:
        services.log_access(request.user, client, HealthAccessLog.ACTION_VIEW, profile.version)
        errors, values = {}, form_values_from_profile(profile, client.training_goal)

    return render(request, 'trainer/health_profile_form.html', _form_context(
        mode='trainer', client=client, profile=profile, values=values, errors=errors,
        last_height=known_height))
