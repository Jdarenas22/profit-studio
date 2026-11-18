"""API interna v1: sesión Django y CSRF; nunca registra cuerpos ni pulsos."""
import json
import uuid

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.db import transaction
from django.http import JsonResponse
from django.views.csrf import csrf_failure as default_csrf_failure
from django.views.decorators.cache import never_cache
from django.utils.cache import add_never_cache_headers
from django.views.decorators.csrf import csrf_protect

from apps.accounts.permissions import clients_for_trainer
from apps.health import services as health_services
from apps.health.models import PURPOSE_HEALTH_DATA
from .cardio import PULSE_FIELDS, PROTOCOL, calculate_indices, serialize_evaluation
from .models import CardioEvaluation, InitialAssessment

MAX_BODY_BYTES = 4096
PAGE_SIZE = 20


def csrf_failure(request, reason=''):
    """El middleware CSRF se ejecuta antes de la vista; los errores de API también son privados."""
    if request.path.startswith('/api/v1/assessments/'):
        response = error('csrf_failed', 'Recarga la página para renovar el token de seguridad.', 403)
        add_never_cache_headers(response)
        return response
    return default_csrf_failure(request, reason=reason)


def error(code, message, status, fields=None):
    return JsonResponse({'error': {'code': code, 'message': message, 'fields': fields or {}}}, status=status)


def validate_payload(data):
    if not isinstance(data, dict):
        return None, {'body': 'Se requiere un objeto JSON.'}
    allowed = {*PULSE_FIELDS, 'request_token', 'protocol_confirmed'}
    errors = {}
    if set(data) - allowed:
        errors['body'] = 'El objeto contiene campos no admitidos.'
    for field in PULSE_FIELDS:
        if type(data.get(field)) is not int or not 20 <= data[field] <= 250:
            errors[field] = 'Introduce un entero entre 20 y 250 bpm.'
    if data.get('protocol_confirmed') is not True:
        errors['protocol_confirmed'] = 'Confirma que se completó el protocolo indicado.'
    try:
        token = uuid.UUID(str(data.get('request_token', '')))
    except (ValueError, AttributeError):
        errors['request_token'] = 'Se requiere un UUID válido para evitar envíos duplicados.'
        token = None
    return token, errors


@never_cache
@csrf_protect
def ruffier_dickson(request, assessment_pk):
    if not request.user.is_authenticated or not request.user.is_active:
        return error('authentication_required', 'Inicia sesión.', 401)
    if not request.user.is_trainer:
        return error('forbidden', 'Solo disponible para entrenadores.', 403)
    if not settings.HEALTH_FEATURES_ENABLED:
        return error('not_found', 'No encontrado.', 404)
    if request.method not in ('GET', 'POST'):
        response = error('method_not_allowed', 'Usa GET o POST.', 405)
        response['Allow'] = 'GET, POST'
        return response

    # Filtra por autorización ANTES de revelar si existe la valoración.
    with transaction.atomic():
        assessment = (InitialAssessment.objects.select_for_update()
                      .filter(pk=assessment_pk, user__in=clients_for_trainer(request.user)).first())
        if assessment is None:
            return error('not_found', 'No encontrado.', 404)
        client = assessment.user
        if assessment.age < 18 or health_services.is_minor(client):
            return error('adults_only', 'Esta evaluación está disponible solo para adultos.', 403)
        consent = health_services.active_consent(client, PURPOSE_HEALTH_DATA)
        if consent is None:
            return error('consent_required', 'Se requiere consentimiento de salud vigente de la clienta.', 403)

        if request.method == 'GET':
            before = request.GET.get('before')
            if before is not None and (not before.isascii() or not before.isdigit() or len(before) > 18):
                return error('invalid_cursor', 'El cursor before debe ser un id positivo.', 400)
            rows = CardioEvaluation.objects.filter(assessment=assessment).order_by('-pk')
            if before is not None:
                rows = rows.filter(pk__lt=int(before))
            rows = list(rows[:PAGE_SIZE + 1])
            next_before = rows[PAGE_SIZE - 1].pk if len(rows) > PAGE_SIZE else None
            health_services.log_access(request.user, client, 'view')
            return JsonResponse({
                'results': [serialize_evaluation(row) for row in rows[:PAGE_SIZE]],
                'next_before': next_before, 'protocol': PROTOCOL,
            })

        if request.content_type != 'application/json':
            return error('unsupported_media_type', 'Usa Content-Type: application/json.', 415)
        try:
            # No leer por completo cuerpos sin límite; también cubre clientes sin Content-Length.
            raw = request.read(MAX_BODY_BYTES + 1)
            if len(raw) > MAX_BODY_BYTES:
                return error('body_too_large', 'El cuerpo supera 4096 bytes.', 413)
            data = json.loads(raw.decode('utf-8'))
        except RequestDataTooBig:
            return error('body_too_large', 'El cuerpo supera el límite.', 413)
        except (ValueError, UnicodeDecodeError, RecursionError):
            return error('invalid_json', 'JSON inválido.', 400)
        token, errors = validate_payload(data)
        if errors:
            return error('validation_error', 'Revisa los campos.', 400, errors)

        existing = CardioEvaluation.objects.filter(assessment=assessment, request_token=token).first()
        if existing is not None:
            if any(getattr(existing, name) != data[name] for name in PULSE_FIELDS):
                return error('idempotency_conflict', 'Ese token ya fue usado con otros pulsos.', 409)
            return JsonResponse(serialize_evaluation(existing), status=200)

        values = {name: data[name] for name in PULSE_FIELDS}
        evaluation = CardioEvaluation.objects.create(
            assessment=assessment, recorded_by=request.user, consent=consent,
            request_token=token, protocol_confirmed=True, **values, **calculate_indices(**values),
        )
        health_services.log_access(request.user, client, 'edit')
        return JsonResponse(serialize_evaluation(evaluation), status=201)

