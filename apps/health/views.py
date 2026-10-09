"""Consentimiento de la clienta (HTTP). La lógica está en services.py.

Solo la clienta con membresía vigente entra aquí; el consentimiento es siempre el suyo
(nunca se recibe el id de otra persona). Las respuestas llevan `Cache-Control: no-store`.
"""
from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.accounts.decorators import member_required

from . import services
from .decorators import health_feature_required
from .models import PURPOSE_CHOICES

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
    else:
        messages.info(request, 'No tenías una autorización activa para esta finalidad.')
    return redirect(_safe_next(request) or DEFAULT_NEXT)
