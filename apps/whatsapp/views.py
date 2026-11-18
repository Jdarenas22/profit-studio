"""Vistas HTTP de los avisos por WhatsApp (contrato, secciones 2.1 y 6). Solo HTTP: la lógica está en
`services.py`, `queue.py` y `webhook.py`.

- `/accounts/whatsapp/...`: siempre sobre `request.user` (clienta o entrenador), sin exigir
  membresía vigente. Con `WHATSAPP_CLOUD_MODE=off` responden 404, salvo la baja (POST), que siempre
  se permite (derecho de la titular). `Cache-Control: no-store` (muestran el teléfono).
- `/whatsapp/webhook/`: Meta, sin sesión. `@csrf_exempt` SOLO aquí; la firma HMAC es la protección.
"""
import functools
import hmac
import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from apps.health import services as health_services

from . import conf, services, webhook
from .forms import WhatsAppEnableForm
from .phone import InvalidPhone, display, normalize_phone

logger = logging.getLogger(__name__)

MEMBER_TEMPLATE = 'accounts/whatsapp_settings.html'
TRAINER_TEMPLATE = 'trainer/whatsapp_settings.html'


def _template(request):
    return TRAINER_TEMPLATE if request.user.is_trainer else MEMBER_TEMPLATE
MAX_WEBHOOK_BYTES = 3_500_000      # Meta documenta cargas de hasta 3 MB
GENERIC_ERROR = 'No pudimos guardar el cambio. Inténtalo de nuevo más tarde.'


def _feature_required(view):
    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        if not conf.enabled():
            raise Http404
        return view(request, *args, **kwargs)
    return wrapped


def _initial_phone(user, contact):
    if contact is not None and contact.phone_e164:
        return display(contact.phone_e164)
    try:
        return display(normalize_phone(user.phone))
    except InvalidPhone:
        return user.phone or ''


def settings_context(request, form=None, errors=None):
    user = request.user
    contact = services.contact_for(user)
    text = services.current_text()
    available = conf.sending_allowed() and text is not None
    status = services.status_for(user)
    return {
        'status': status,
        'available': available,
        'text_ready': available,
        'phone_display': display(contact.phone_e164) if contact is not None and status != 'off' else '',
        'form': form if form is not None else {'phone': _initial_phone(user, contact), 'accept': False},
        'errors': errors or {},
        'text_version': text.version if text is not None else None,
        'body': text.body if text is not None else '',
        'can_resend': status == 'pending' and services.can_resend(user),
        'recent': services.recent_for(user),
        'privacy_policy_url': getattr(settings, 'PRIVACY_POLICY_URL', '') or '',
        'is_minor': health_services.is_minor(user),
        'is_simulated': conf.mode() == conf.MODE_SIMULATED,
    }


@never_cache
@login_required
@_feature_required
@require_http_methods(['GET'])
def whatsapp_settings(request):
    return render(request, _template(request), settings_context(request))


@never_cache
@login_required
@_feature_required
@require_POST
def whatsapp_enable(request):
    form = WhatsAppEnableForm(request.POST)
    raw = {'phone': request.POST.get('phone', '')[:30], 'accept': request.POST.get('accept') == 'on'}
    if not form.is_valid():
        errors = {field: ' '.join(str(e) for e in errs) for field, errs in form.errors.items()}
        errors.pop('__all__', None)
        if 'text_version' in errors:
            errors['general'] = errors.pop('text_version')
        return render(request, _template(request), settings_context(request, raw, errors))
    try:
        services.enable(request.user, form.cleaned_data['phone'], form.cleaned_data['text_version'])
    except services.WhatsAppError as exc:
        return render(request, _template(request), settings_context(request, raw, {exc.field: exc.message}))
    except Exception as exc:   # noqa: BLE001
        logger.error('whatsapp enable failed error=%s', type(exc).__name__)
        messages.error(request, GENERIC_ERROR)
        return redirect('whatsapp_settings')
    messages.success(request, 'Listo. Te enviamos un mensaje por WhatsApp: pulsa "Sí, soy yo" para '
                              'empezar a recibir los avisos.')
    return redirect('whatsapp_settings')


@never_cache
@login_required
@require_POST
def whatsapp_disable(request):
    """Siempre permitida, aunque el modo esté apagado (derecho de la titular)."""
    try:
        services.disable(request.user)
    except Exception as exc:   # noqa: BLE001
        logger.error('whatsapp disable failed error=%s', type(exc).__name__)
        messages.error(request, GENERIC_ERROR)
    else:
        messages.success(request, 'Desactivaste los avisos por WhatsApp. No te enviaremos más mensajes.')
    if not conf.enabled():
        return redirect('dashboard')
    return redirect('whatsapp_settings')


@never_cache
@login_required
@_feature_required
@require_POST
def whatsapp_resend_confirmation(request):
    try:
        services.resend_confirmation(request.user)
    except services.WhatsAppError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, 'Te enviamos de nuevo el mensaje de confirmación.')
    return redirect('whatsapp_settings')


# ─── Webhook de Meta ──────────────────────────────────────────────────────────

def _verify(request):
    token = getattr(settings, 'WHATSAPP_CLOUD_VERIFY_TOKEN', '') or ''
    if not token:
        logger.error('whatsapp webhook: WHATSAPP_CLOUD_VERIFY_TOKEN no configurado')
        return HttpResponse(status=503)
    mode = request.GET.get('hub.mode')
    received = request.GET.get('hub.verify_token')
    challenge = request.GET.get('hub.challenge')
    if not mode or received is None or not challenge:
        return HttpResponse(status=400)
    if mode != 'subscribe' or not hmac.compare_digest(received.encode('utf-8'), token.encode('utf-8')):
        logger.warning('whatsapp webhook: verify token rejected')
        return HttpResponse(status=403)
    if not webhook.valid_challenge(challenge):
        return HttpResponse(status=400)
    return HttpResponse(challenge, content_type='text/plain')


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def whatsapp_webhook(request):
    """Códigos: 200 procesado/duplicado/ignorado (o JSON inválido pero firmado: reintentar no lo
    arregla), 401 firma ausente o inválida, 413 cuerpo demasiado grande, 503 secreto sin configurar,
    500 fallo de la base (Meta reintenta; todo es idempotente)."""
    if not conf.enabled():
        raise Http404
    if request.method == 'GET':
        return _verify(request)
    secret = getattr(settings, 'WHATSAPP_CLOUD_APP_SECRET', '') or ''
    if not secret:
        logger.error('whatsapp webhook: WHATSAPP_CLOUD_APP_SECRET no configurado')
        return HttpResponse(status=503)
    try:
        length = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        length = 0
    if length > MAX_WEBHOOK_BYTES:
        logger.warning('whatsapp webhook: body too large bytes=%s', length)
        return HttpResponse(status=413)
    # Se lee el flujo directamente (no `request.body`, que corta en DATA_UPLOAD_MAX_MEMORY_SIZE = 2,5 MB)
    raw = request.read(MAX_WEBHOOK_BYTES + 1)
    if len(raw) > MAX_WEBHOOK_BYTES:
        return HttpResponse(status=413)
    if not webhook.valid_signature(raw, request.headers.get('X-Hub-Signature-256'), secret):
        logger.warning('whatsapp webhook: signature rejected')
        return HttpResponse(status=401)
    try:
        data = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        logger.warning('whatsapp webhook: signed body is not valid JSON')
        return HttpResponse('OK')
    try:
        summary = webhook.process_payload(data)
    except Exception as exc:   # noqa: BLE001 — la transacción se revierte y Meta reintenta
        logger.error('whatsapp webhook: processing failed error=%s', type(exc).__name__)
        return HttpResponse(status=500)
    logger.info('whatsapp webhook processed statuses=%s messages=%s ignored=%s',
                summary['statuses'], summary['messages'], summary['ignored_entries'])
    return HttpResponse('OK')
