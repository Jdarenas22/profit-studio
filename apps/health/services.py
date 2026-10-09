"""Casos de uso del consentimiento (lógica de negocio, sin HTTP).

Reglas (docs/contracts/plan-ia.md, secciones 2.2, 2.3 y 9):
- Solo la propia clienta otorga o retira su consentimiento (las vistas pasan `request.user`;
  ninguna función recibe "otra" persona desde la URL).
- Un consentimiento vale mientras no esté retirado Y su texto siga siendo el vigente: si se
  publica una versión nueva, hay que aceptarla de nuevo antes del siguiente uso.
- Se guarda fecha, usuario y versión. No se guarda IP.

No se escribe contenido de salud en logs; solo finalidad, versión e id de usuario.

Ficha de salud y alimentación (fase A2, secciones 2.2, 2.3, 9 y 13 del contrato):
- Guardar la ficha exige el consentimiento `health_data` vigente (no el de IA).
- La clienta guarda la suya; el entrenador solo CORRIGE una ficha existente (no la crea) de una
  clienta que puede ver. Cada guardado crea una versión nueva y deja una sola `is_current`.
- Una ficha corregida por el entrenador queda sin confirmar hasta que la clienta la confirme.
- Ver, exportar y borrar la ficha propia no exigen consentimiento (son derechos de la titular).
- `HealthAccessLog` registra qué hizo el entrenador (ver/editar) y la clienta (exportar/borrar).
"""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from apps.accounts.permissions import clients_for_trainer
from apps.assessments.models import BodyMeasurement, InitialAssessment
from apps.assessments.services import latest_anthropometrics
from apps.plans.rules import flags as rules_flags

from . import choices as ch
from . import dates
from .models import (
    PURPOSE_AI, PURPOSE_CHOICES, PURPOSE_HEALTH_DATA, PURPOSES, ConsentRecord, ConsentTextVersion,
    HealthAccessLog, HealthProfile,
)

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


def consent_cards(user):
    """Estado de cada finalidad para la tarjeta "Consentimientos" (F-05): una tarjeta por
    finalidad con `active`, fecha y versión aceptadas, y `outdated` si aceptó un texto que ya no
    es el vigente (debe aceptar el nuevo)."""
    cards = []
    for purpose in PURPOSES:
        record = active_consent(user, purpose)
        text = current_text(purpose)
        outdated = record is None and ConsentRecord.objects.filter(
            user=user, text_version__purpose=purpose, revoked_at__isnull=True).exists()
        cards.append({
            'purpose': purpose,
            'label': dict(PURPOSE_CHOICES)[purpose],
            'active': record is not None,
            'granted_at': record.granted_at if record else None,
            'version': record.text_version.version if record else None,
            'current_version': text.version if text else None,
            'outdated': outdated,
        })
    return cards


# ─── Menores de edad ──────────────────────────────────────────────────────────

def is_minor(user):
    """True si lo que se sabe de la clienta indica que tiene menos de 18 años.

    Manda la fecha de nacimiento de su ficha vigente (fase A2). Si todavía no tiene ficha, se usa
    como respaldo la edad de su última valoración inicial (la registra el entrenador), así una
    menor identificada por el entrenador nunca llega a crear ficha. Sin ningún dato no se
    bloquea: el texto del consentimiento incluye la declaración "soy mayor de 18 años".
    """
    profile = current_profile(user)
    if profile is not None:
        return dates.age_today(profile.birth_date) < ADULT_AGE
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


# ─── Ficha de salud y alimentación (fase A2) ──────────────────────────────────

# Campos de `HealthProfile` que entrega el formulario (más `training_goal`, que vive en User)
PROFILE_FIELDS = (
    'birth_date', 'sex_for_calculation', 'target_weight_kg',
    'conditions', 'condition_controlled', 'medical_clearance', 'clearance_date', 'medications',
    'injuries', 'recent_surgery', 'pregnancy_status', 'eating_disorder_history', 'parq',
    'other_condition_text', 'medications_text', 'injuries_text', 'disliked_foods_text', 'liked_foods_text',
    'diet_type', 'allergies', 'allergy_severity', 'intolerances', 'meal_slots', 'meal_times',
    'cooking_access', 'budget_level', 'eats_out_per_week',
    'training_days_per_week', 'session_minutes', 'training_place', 'equipment',
    'experience_level', 'activity_level', 'sleep_hours',
)
MAX_AGE = 100
EXPORT_LOG_LIMIT = 500


class ProfileError(Exception):
    """Base de los errores de negocio de la ficha (mensaje apto para mostrar)."""


class ProfileConsentRequired(ProfileError):
    pass


class ProfileNotFound(ProfileError):
    pass


class ProfileInvalid(ProfileError):
    pass


class ProfileNotAllowed(ProfileError):
    pass


class ProfileConflict(ProfileError):
    pass


def today():
    return dates.today()


def current_profile(user):
    """Versión vigente de la ficha de `user`, o None."""
    return HealthProfile.objects.filter(user=user, is_current=True).first()


def profile_versions(user):
    """Resumen de todas las versiones (más reciente primero), sin el contenido de la ficha."""
    return list(HealthProfile.objects.filter(user=user)
                .values('version', 'is_current', 'created_at', 'created_by_role', 'confirmed_by_client_at'))


def log_access(actor, client, action, profile_version=None):
    """Deja constancia de quién hizo qué con la ficha. Solo ids: nunca contenido de salud."""
    entry = HealthAccessLog.objects.create(
        actor=actor, client=client, action=action, profile_version=profile_version,
    )
    logger.info('health access action=%s client_id=%s actor_id=%s',
                action, client.pk, getattr(actor, 'pk', None))
    return entry


def _check_profile_data(data):
    """Última defensa en el servicio (el formulario ya validó lo demás)."""
    missing = [name for name in (*PROFILE_FIELDS, 'training_goal') if name not in data]
    if missing:
        raise ProfileInvalid('Faltan datos de la ficha.')
    age = dates.age_today(data['birth_date'])
    if age < ADULT_AGE:
        raise ProfileInvalid('La ficha en línea es solo para mayores de 18 años.')
    if age > MAX_AGE:
        raise ProfileInvalid('Revisa la fecha de nacimiento.')
    slots = list(data['meal_slots'])
    if (len(set(slots)) != len(slots) or not set(slots) <= set(ch.codes(ch.MEAL_SLOT_CHOICES))
            or any(code not in slots for code in ch.REQUIRED_MEAL_SLOTS)
            or not ch.MEAL_SLOTS_MIN <= len(slots) <= ch.MEAL_SLOTS_MAX):
        raise ProfileInvalid('Las comidas deben incluir desayuno, almuerzo y cena (de 3 a 5 en total).')
    if data['training_goal'] not in dict(get_user_model().GOAL_CHOICES):
        raise ProfileInvalid('Elige una meta válida.')


def save_profile(user, data, actor):
    """Guarda una versión NUEVA de la ficha de `user` (datos ya validados por `HealthProfileForm`).

    - La clienta (`actor == user`) guarda la suya; queda confirmada por ella.
    - Un entrenador (o la superusuaria) solo corrige la ficha de una clienta que puede ver y que
      ya tiene ficha; la versión nueva queda SIN confirmar (la clienta debe confirmarla).
    - Exige el consentimiento `health_data` vigente de la clienta.
    - Actualiza la meta en `User.training_goal` (no se duplica en la ficha).
    Devuelve la `HealthProfile` nueva.
    """
    _check_profile_data(data)
    if actor.pk == user.pk:
        if user.is_trainer:
            raise ProfileNotAllowed('Este usuario no tiene ficha de salud.')
        role = HealthProfile.ROLE_MEMBER
    elif actor.is_trainer:
        if not clients_for_trainer(actor).filter(pk=user.pk).exists():
            raise ProfileNotAllowed('No tienes acceso a esta clienta.')
        role = HealthProfile.ROLE_TRAINER
    else:
        raise ProfileNotAllowed('No tienes acceso a esta ficha.')

    try:
        with transaction.atomic():
            # Serializa los guardados de la misma clienta (en SQLite no hace nada: allí la
            # restricción única parcial detiene la carrera)
            get_user_model().objects.select_for_update().get(pk=user.pk)
            consent = active_consent(user, PURPOSE_HEALTH_DATA)
            if consent is None:
                raise ProfileConsentRequired('Falta la autorización vigente para tratar los datos de salud.')
            previous = HealthProfile.objects.filter(user=user, is_current=True).first()
            if role == HealthProfile.ROLE_TRAINER and previous is None:
                raise ProfileNotFound('La clienta aún no ha llenado su ficha: el entrenador solo puede corregirla.')
            version = (HealthProfile.objects.filter(user=user).aggregate(last=Max('version'))['last'] or 0) + 1
            HealthProfile.objects.filter(user=user, is_current=True).update(is_current=False)
            profile = HealthProfile.objects.create(
                user=user, version=version, is_current=True,
                created_by=actor, created_by_role=role, consent=consent,
                confirmed_by_client_at=timezone.now() if role == HealthProfile.ROLE_MEMBER else None,
                **{name: data[name] for name in PROFILE_FIELDS},
            )
            get_user_model().objects.filter(pk=user.pk).update(training_goal=data['training_goal'])
            user.training_goal = data['training_goal']
            if role == HealthProfile.ROLE_TRAINER:
                log_access(actor, user, HealthAccessLog.ACTION_EDIT, version)
    except IntegrityError as exc:   # dos guardados a la vez (SQLite) o versión repetida
        raise ProfileConflict('Se guardó otra versión al mismo tiempo. Revisa tus datos e inténtalo de nuevo.') from exc
    logger.info('health profile saved user_id=%s version=%s role=%s', user.pk, version, role)
    return profile


def confirm_profile(user):
    """La clienta confirma su ficha vigente (la corrigió el entrenador). Idempotente.
    Devuelve (ficha, cambió)."""
    if active_consent(user, PURPOSE_HEALTH_DATA) is None:
        raise ProfileConsentRequired('Falta la autorización vigente para tratar los datos de salud.')
    profile = current_profile(user)
    if profile is None:
        raise ProfileNotFound('Aún no tienes ficha de salud.')
    if profile.confirmed_by_client_at is not None:
        return profile, False
    updated = HealthProfile.objects.filter(pk=profile.pk, confirmed_by_client_at__isnull=True).update(
        confirmed_by_client_at=timezone.now())
    profile.refresh_from_db()
    if updated:
        logger.info('health profile confirmed user_id=%s version=%s', user.pk, profile.version)
    return profile, bool(updated)


def delete_profile(user):
    """Borra TODAS las versiones de la ficha de `user` (derecho de supresión). Deja constancia en
    `HealthAccessLog` (sin contenido). Devuelve cuántas versiones borró."""
    with transaction.atomic():
        versions = list(HealthProfile.objects.filter(user=user).values_list('version', flat=True))
        if not versions:
            return 0
        HealthProfile.objects.filter(user=user).delete()
        log_access(user, user, HealthAccessLog.ACTION_DELETE, max(versions))
    logger.info('health profile deleted user_id=%s versions=%s', user.pk, len(versions))
    return len(versions)


def has_profile(user):
    return HealthProfile.objects.filter(user=user).exists()


def _profile_export(profile):
    data = {name: getattr(profile, name) for name in PROFILE_FIELDS}
    data.update(
        version=profile.version, is_current=profile.is_current, created_at=profile.created_at,
        created_by_role=profile.created_by_role, confirmed_by_client_at=profile.confirmed_by_client_at,
    )
    return data


def export_data(user):
    """Todos los datos de salud propios de `user` en un diccionario (se entrega como JSON).
    Deja constancia en `HealthAccessLog`. Los valores pueden incluir Decimal/fechas: la vista
    los serializa con DjangoJSONEncoder."""
    measurements = [
        {'date': m.date, 'weight_kg': m.weight, 'height_m': m.height, 'waist_cm': m.waist_cm,
         'hip_cm': m.hip_cm, 'body_fat_pct': m.body_fat_pct, 'imc': m.imc,
         'source': m.source, 'notes': m.notes}
        for m in BodyMeasurement.objects.filter(user=user).order_by('date', 'pk')
    ]
    consents = [
        {'purpose': record.text_version.purpose, 'text_version': record.text_version.version,
         'granted_at': record.granted_at, 'revoked_at': record.revoked_at}
        for record in consent_history(user)
    ]
    access = [
        {'action': entry.action, 'profile_version': entry.profile_version, 'at': entry.created_at,
         'by': 'tu' if entry.actor_id == user.pk else 'entrenador'}
        for entry in HealthAccessLog.objects.filter(client=user).order_by('-created_at', '-pk')[:EXPORT_LOG_LIMIT]
    ]
    payload = {
        'format': 'profit-studio-health-export/1',
        'generated_at': timezone.now(),
        'account': {
            'username': user.username, 'first_name': user.first_name, 'last_name': user.last_name,
            'email': user.email, 'training_goal': user.training_goal,
        },
        'health_profiles': [_profile_export(p) for p in HealthProfile.objects.filter(user=user).order_by('version')],
        'body_measurements': measurements,
        'consents': consents,
        'access_log': access,
    }
    log_access(user, user, HealthAccessLog.ACTION_EXPORT, getattr(current_profile(user), 'version', None))
    return payload


def trainer_can_see_content(client):
    """El entrenador solo ve el contenido de la ficha si la clienta tiene el consentimiento
    `health_data` vigente (si lo retiró o hay un texto nuevo sin aceptar, no)."""
    return has_health_consent(client)


# ─── Banderas (solo para el entrenador) ───────────────────────────────────────

def build_facts(user, profile):
    """Arma los `Facts` del motor de reglas desde la ficha, las medidas y los consentimientos."""
    anth = latest_anthropometrics(user)
    assessment_age = (InitialAssessment.objects.filter(user=user)
                      .order_by('-date', '-pk').values_list('age', flat=True).first())
    common = dict(
        assessment_age=assessment_age,
        weight_kg=anth.weight if anth else None,
        height_m=anth.height if anth else None,
        measurement_days_old=anth.days_old if anth else None,
        ai_consent=has_valid_consent(user, PURPOSE_AI),
        today=dates.today(),
    )
    if profile is None:
        return rules_flags.Facts(has_profile=False, goal=user.training_goal, **common)
    return rules_flags.Facts(
        age=dates.age_today(profile.birth_date),
        sex=profile.sex_for_calculation,
        goal=user.training_goal,
        conditions=tuple(profile.conditions or ()),
        condition_controlled=profile.condition_controlled,
        medical_clearance=profile.medical_clearance,
        clearance_date=profile.clearance_date,
        medications=tuple(profile.medications or ()),
        injuries=tuple(profile.injuries or ()),
        recent_surgery=profile.recent_surgery,
        pregnancy_status=profile.pregnancy_status,
        eating_disorder_history=profile.eating_disorder_history,
        parq=dict(profile.parq or {}),
        has_other_text=bool(profile.other_condition_text or profile.medications_text),
        allergies=tuple(profile.allergies or ()),
        allergy_severity=profile.allergy_severity,
        intolerances=tuple(profile.intolerances or ()),
        sleep_hours=profile.sleep_hours,
        has_profile=True,
        profile_confirmed=profile.confirmed_by_client_at is not None,
        **common,
    )


def profile_flags(user, profile=None):
    """Banderas calculadas (`FlagReport`) de la clienta. Solo para el panel del entrenador."""
    profile = profile if profile is not None else current_profile(user)
    return rules_flags.evaluate(build_facts(user, profile))
