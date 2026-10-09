"""Casos de uso del consentimiento (lógica de negocio, sin HTTP).

Reglas (docs/contracts/plan-ia.md, secciones 2.2, 2.3 y 9):
- Solo la propia clienta otorga o retira su consentimiento (las vistas pasan `request.user`;
  ninguna función recibe "otra" persona desde la URL).
- Un consentimiento vale mientras no esté retirado Y su texto siga siendo el vigente: si se
  publica una versión nueva, hay que aceptarla de nuevo antes del siguiente uso.
- Se guarda fecha, usuario y versión. No se guarda IP.

No se escribe contenido de salud en logs; solo finalidad, versión e id de usuario.
"""
import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.assessments.models import InitialAssessment

from .models import PURPOSE_HEALTH_DATA, PURPOSES, ConsentRecord, ConsentTextVersion

logger = logging.getLogger(__name__)

ADULT_AGE = 18


class ConsentError(Exception):
    """Base de los errores de negocio del consentimiento (mensaje apto para mostrar)."""


class ConsentTextUnavailable(ConsentError):
    pass


class ConsentTextChanged(ConsentError):
    pass


def health_features_enabled():
    """Interruptor de las funciones de salud de la clienta (falso por defecto en producción)."""
    return bool(getattr(settings, 'HEALTH_FEATURES_ENABLED', False))


def is_valid_purpose(purpose):
    return purpose in PURPOSES


def current_text(purpose):
    return ConsentTextVersion.objects.filter(purpose=purpose, is_current=True).first()


def active_consent(user, purpose):
    """Consentimiento vigente de `user` para `purpose` (no retirado y sobre el texto vigente)."""
    return (ConsentRecord.objects
            .filter(user=user, text_version__purpose=purpose, text_version__is_current=True,
                    revoked_at__isnull=True)
            .select_related('text_version')
            .order_by('-granted_at', '-pk').first())


def has_valid_consent(user, purpose):
    return active_consent(user, purpose) is not None


def has_health_consent(user):
    return has_valid_consent(user, PURPOSE_HEALTH_DATA)


def grant_consent(user, purpose, accepted_version):
    """Registra la aceptación del texto vigente. `accepted_version` es el número de versión que
    la clienta tenía en pantalla: si ya cambió, no se registra (debe leer el texto nuevo).
    Es idempotente (doble clic): devuelve el registro existente. Retorna (registro, creado)."""
    text = current_text(purpose)
    if text is None:
        raise ConsentTextUnavailable('Este consentimiento aún no está disponible.')
    if accepted_version != text.version:
        raise ConsentTextChanged('El texto cambió mientras lo leías. Revisa la versión actual y vuelve a aceptar.')
    try:
        with transaction.atomic():
            record, created = ConsentRecord.objects.get_or_create(
                user=user, text_version=text, revoked_at=None,
                defaults={'granted_at': timezone.now()},
            )
    except IntegrityError:   # carrera entre dos envíos simultáneos
        record = ConsentRecord.objects.get(user=user, text_version=text, revoked_at__isnull=True)
        created = False
    if created:
        logger.info('consent granted purpose=%s version=%s user_id=%s', purpose, text.version, user.pk)
    return record, created


def revoke_consent(user, purpose):
    """Retira todos los consentimientos activos de la finalidad. Devuelve cuántos retiró."""
    count = ConsentRecord.objects.filter(
        user=user, text_version__purpose=purpose, revoked_at__isnull=True,
    ).update(revoked_at=timezone.now())
    if count:
        logger.info('consent revoked purpose=%s user_id=%s', purpose, user.pk)
    return count


def consent_history(user):
    """Historial completo de la clienta (más reciente primero)."""
    return ConsentRecord.objects.filter(user=user).select_related('text_version')


# ─── Menores de edad ──────────────────────────────────────────────────────────

def is_minor(user):
    """True si lo que se sabe de la clienta indica que tiene menos de 18 años.

    Hoy (fase A1) el único dato es la edad de su última valoración inicial (la registra el
    entrenador). La fecha de nacimiento de la ficha (fase A2) la reemplazará. Sin dato, no se
    bloquea: el texto del consentimiento incluye la declaración "soy mayor de 18 años".
    """
    age = (InitialAssessment.objects.filter(user=user)
           .order_by('-date', '-pk').values_list('age', flat=True).first())
    return age is not None and age < ADULT_AGE


# ─── Textos pendientes de revisión legal ──────────────────────────────────────

def purposes_with_placeholders():
    """Finalidades cuyo texto vigente aún tiene marcadores `[...]` (o no existe)."""
    pending = []
    for purpose in PURPOSES:
        text = current_text(purpose)
        if text is None or text.has_placeholders:
            pending.append(purpose)
    return pending


def ai_texts_ready():
    """¿Se puede enviar algo a la IA con los textos legales actuales?

    La fase B debe llamar esto antes de crear un trabajo de IA: en producción (no DEBUG)
    exige que el texto vigente de `ai_processing` y el de `health_data` ya no tengan marcadores
    `[...]` (los reemplaza la superusuaria al publicar la versión revisada por el abogado).
    En desarrollo (DEBUG) no bloquea, para poder probar con el texto de borrador.
    """
    if settings.DEBUG:
        return True
    return not purposes_with_placeholders()
