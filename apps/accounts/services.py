"""Casos de uso del ciclo de vida de las cuentas: desactivar, reactivar, eliminar definitivamente y
transferir la administración.

Contratos: docs/contracts/cuentas-eliminar.md (aprobado el 2026-11-17 con las respuestas 1a a 10a) y
docs/contracts/transferir-administracion.md.

- Las vistas solo manejan HTTP; las reglas de permiso se repiten aquí (defensa en profundidad):
  nunca sobre una superusuaria, nunca sobre la propia cuenta, nunca sobre una cuenta eliminada, y
  siempre queda al menos una superusuaria activa.
- Todo pasa en UNA transacción con `select_for_update()` sobre la cuenta afectada (y sobre el
  entrenador destino de la reasignación). Si algo falla, no cambia nada.
- Eliminar deja una LÁPIDA: la fila de `User` vaciada, sin datos personales, para que pagos,
  auditoría y constancias no pierdan su referencia (respuesta 3a).
- Los archivos se borran con `transaction.on_commit` (solo si la transacción se confirmó).
- Los logs solo llevan ids, rol, acción y códigos: nunca nombres, correos ni lo escrito en la
  confirmación.
"""
import logging
import unicodedata
from dataclasses import dataclass, field

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import choices
from .models import AccountAuditLog, User

logger = logging.getLogger(__name__)

GENERIC_ERROR = 'No pudimos completar la acción. No se cambió nada. Inténtalo de nuevo.'
NOT_ALLOWED_MESSAGE = 'No se puede hacer esto con esta cuenta.'
REASSIGN_REQUIRED_MESSAGE = 'Elige a quién pasar sus clientas.'
REASSIGN_UNAVAILABLE_MESSAGE = 'Ese entrenador ya no está disponible. Elige otro.'
REASON_REQUIRED_MESSAGE = 'Elige un motivo.'

TRANSFER_PASSWORD_MESSAGE = 'La contraseña no es correcta.'
TRANSFER_TARGET_MESSAGE = 'Esa persona ya no está disponible. Elige otra.'

ERASED_TEXT = '[dato eliminado]'
TOMBSTONE_USERNAME = 'cuenta-eliminada-{pk}'

# Lo único que queda del cuerpo de un webhook de Wompi de una cuenta eliminada (sección 7.1, punto 11)
WEBHOOK_TRANSACTION_WHITELIST = (
    'id', 'reference', 'status', 'amount_in_cents', 'currency', 'payment_method_type',
    'created_at', 'finalized_at',
)

# ─── Qué se hace con cada clave foránea a User al eliminar (sección 7.1) ──────
# Toda clave foránea nueva a `User` debe clasificarse aquí: la prueba
# `test_account_lifecycle.ForeignKeyInventoryTests` falla si aparece una sin clasificar.
#   delete    se borra (dato de la persona eliminada)
#   keep      se conserva apuntando a la lápida (historial, trazabilidad)
#   anonymize se conserva apuntando a la lápida y se le quitan los datos personales
#   reassign  pasa a otra cuenta antes de eliminar
DELETE, KEEP, ANONYMIZE, REASSIGN = 'delete', 'keep', 'anonymize', 'reassign'
ERASE_POLICY = {
    # Se borran, en este orden (primero lo que depende de lo siguiente)
    'plans.MealChoice.client': DELETE,
    'plans.PlanGenerationJob.client': DELETE,
    'plans.Plan.client': DELETE,
    'routines.RoutineDayLog.user': DELETE,
    'routines.Routine.user': DELETE,
    'health.HealthProfile.user': DELETE,
    'assessments.InitialAssessment.user': DELETE,
    'assessments.BodyMeasurement.user': DELETE,
    'memberships.Membership.user': DELETE,
    'whatsapp.WhatsAppMessage.recipient': DELETE,
    'whatsapp.WhatsAppContact.user': DELETE,
    'plans.TrainerCredential.user': DELETE,
    # Se conservan sin datos personales
    'payments.ManualPayment.user': ANONYMIZE,
    'payments.Payment.user': ANONYMIZE,
    'health.ConsentRecord.user': ANONYMIZE,
    # Se conservan apuntando a la lápida
    'health.HealthAccessLog.client': KEEP,
    'admin.LogEntry.user': KEEP,
    'routines.Routine.trainer': KEEP,
    'assessments.InitialAssessment.trainer': KEEP,
    'assessments.BodyMeasurement.trainer': KEEP,
    'assessments.CardioEvaluation.recorded_by': KEEP,
    'health.HealthAccessLog.actor': KEEP,
    'health.HealthProfile.created_by': KEEP,
    'memberships.Membership.activated_by': KEEP,
    'payments.ManualPayment.trainer': KEEP,
    'plans.Plan.approved_by': KEEP,
    'plans.Plan.reviewed_by': KEEP,
    'plans.Plan.warnings_ack_by': KEEP,
    'plans.PlanEvent.actor': KEEP,
    'plans.PlanGenerationJob.requested_by': KEEP,
    'accounts.AccountAuditLog.actor': KEEP,
    'accounts.AccountAuditLog.target': KEEP,
    'accounts.AccountAuditLog.reassigned_to': KEEP,
    # Pasa a otro entrenador antes de eliminar (sección 8)
    'accounts.User.assigned_trainer': REASSIGN,
}


def user_foreign_keys():
    """Todas las claves foráneas (y uno a uno) concretas que apuntan a `User`, como
    `'app.Modelo.campo'`. Incluye las ocultas (`related_name='+'`)."""
    from django.apps import apps
    keys = set()
    for model in apps.get_models():
        for model_field in model._meta.get_fields():
            if (getattr(model_field, 'concrete', False) and model_field.is_relation
                    and (model_field.many_to_one or model_field.one_to_one)
                    and model_field.related_model is User):
                keys.add(f'{model._meta.label}.{model_field.name}')
    return keys


# ─── Errores ──────────────────────────────────────────────────────────────────

class AccountActionError(Exception):
    """La acción no se puede hacer. `code` va a los logs; `message`, a la pantalla."""
    message = NOT_ALLOWED_MESSAGE

    def __init__(self, code, message=None):
        super().__init__(code)
        self.code = code
        if message is not None:
            self.message = message


class AccountNotAllowed(AccountActionError):
    """Códigos: `account_protected` (superusuaria), `account_self`, `account_erased`, `account_pending`
    (registro sin confirmar), `last_superuser`, `not_allowed`."""


class AccountFieldError(AccountActionError):
    """Error de un campo del formulario detectado dentro de la transacción (carrera)."""

    def __init__(self, code, field_name, message):
        super().__init__(code, message)
        self.field = field_name


@dataclass
class ActionResult:
    changed: bool
    reassigned_count: int = 0
    released_count: int = 0
    reassigned_to: object = None
    extra: dict = field(default_factory=dict)


# ─── Permisos (sección 2) ─────────────────────────────────────────────────────

def check_can_act(actor, target, action):
    """Lanza `AccountNotAllowed` si `actor` no puede hacer `action` sobre `target`."""
    if not (actor is not None and getattr(actor, 'is_authenticated', False) and actor.is_trainer
            and actor.is_active and not actor.is_erased):
        raise AccountNotAllowed('not_allowed')
    if target.is_erased:
        raise AccountNotAllowed('account_erased')
    if target.is_pending_registration:
        # Registrada que aún no confirma su correo: no es una clienta del panel (docs/contracts/verificacion-correo.md).
        # Reactivarla la activaría sin verificar el correo; se limpia sola con `purge_unverified_accounts`.
        raise AccountNotAllowed('account_pending')
    if target.is_superuser:
        raise AccountNotAllowed('account_protected')
    if target.pk == actor.pk:
        raise AccountNotAllowed('account_self')
    if target.is_trainer:
        if not actor.is_superuser:
            raise AccountNotAllowed('not_allowed')
        return
    if target.role != User.ROLE_MEMBER:
        raise AccountNotAllowed('not_allowed')
    if action == choices.ACTION_ERASE and not actor.is_superuser:
        raise AccountNotAllowed('not_allowed')
    if not actor.is_superuser and target.assigned_trainer_id != actor.pk:
        raise AccountNotAllowed('not_allowed')


def can_act(actor, target, action):
    try:
        check_can_act(actor, target, action)
    except AccountNotAllowed:
        return False
    return True


def active_superusers():
    return User.objects.filter(role=User.ROLE_TRAINER, is_superuser=True, is_active=True,
                               erased_at__isnull=True)


def _ensure_superuser_left():
    """Invariante: después de la acción queda al menos una superusuaria activa."""
    if not active_superusers().exists():
        raise AccountNotAllowed('last_superuser')


# ─── Ayudantes ────────────────────────────────────────────────────────────────

def normalize_name(value):
    """Para comparar el nombre escrito: sin espacios sobrantes y sin distinguir mayúsculas. Las
    tildes SÍ cuentan (solo se unifica su codificación Unicode)."""
    value = unicodedata.normalize('NFC', value or '')
    return ' '.join(value.split()).casefold()


def name_matches(typed, expected):
    expected_norm = normalize_name(expected)
    return bool(expected_norm) and normalize_name(typed) == expected_norm


def clean_reason(reason, required):
    reason = (reason or '').strip()
    if not reason:
        if required:
            raise AccountFieldError('reason_required', 'reason', REASON_REQUIRED_MESSAGE)
        return ''
    if reason not in choices.REASON_CODES:
        raise AccountFieldError('reason_invalid', 'reason', REASON_REQUIRED_MESSAGE)
    return reason


def valid_reassign_targets(trainer):
    """Entrenadores activos a los que se pueden pasar las clientas de `trainer`: la superusuaria
    primero, después por nombre."""
    return (User.objects.filter(role=User.ROLE_TRAINER, is_active=True, erased_at__isnull=True)
            .exclude(pk=trainer.pk).order_by('-is_superuser', 'first_name', 'last_name', 'pk'))


def _lock_destination(trainer, reassign_to):
    """Bloquea y valida el destino de las clientas. Devuelve `(destino, cuántas clientas)`."""
    count = User.objects.filter(assigned_trainer=trainer).count()
    if count == 0:
        return None, 0
    if reassign_to is None or reassign_to == '':
        raise AccountFieldError('reassign_required', 'reassign_to', REASSIGN_REQUIRED_MESSAGE)
    dest_pk = getattr(reassign_to, 'pk', reassign_to)
    destination = User.objects.select_for_update().filter(pk=dest_pk).first()
    if (destination is None or not destination.is_trainer or not destination.is_active
            or destination.is_erased or destination.pk == trainer.pk):
        raise AccountFieldError('reassign_unavailable', 'reassign_to', REASSIGN_UNAVAILABLE_MESSAGE)
    return destination, count


def reassign_clients(trainer, destination):
    """Todas las clientas de `trainer` (también las desactivadas) pasan a `destination`."""
    return User.objects.filter(assigned_trainer=trainer).update(assigned_trainer=destination)


def release_in_review_plans(trainer, actor):
    """Los planes que `trainer` estaba revisando vuelven a la bandeja (`draft_ai`)."""
    from apps.plans.models import Plan, PlanEvent
    plans = list(Plan.objects.select_for_update().filter(status=Plan.STATUS_IN_REVIEW, reviewed_by=trainer))
    for plan in plans:
        Plan.objects.filter(pk=plan.pk).update(status=Plan.STATUS_DRAFT_AI, reviewed_by=None)
        PlanEvent.objects.create(plan=plan, actor=actor, action=PlanEvent.ACTION_RELEASED)
    return len(plans)


def cancel_whatsapp_queue(user):
    """Avisos de WhatsApp en cola de `user` -> `cancelled` (`user_inactive`)."""
    from apps.whatsapp.models import WhatsAppMessage as M
    return M.objects.filter(recipient=user).filter(
        Q(status=M.STATUS_QUEUED) | Q(status=M.STATUS_SENDING, wamid__isnull=True),
    ).update(status=M.STATUS_CANCELLED, error_code='user_inactive', locked_at=None)


def cancel_plan_jobs(client):
    """Trabajos de IA en cola de la clienta -> `failed` (`client_inactive`). Los que están
    corriendo terminan, pero `jobs` no guarda el plan (revisa la cuenta antes de guardar)."""
    from apps.plans.models import PlanGenerationJob as J
    return J.objects.filter(client=client, status=J.STATUS_QUEUED).update(
        status=J.STATUS_FAILED, error_code='client_inactive', finished_at=timezone.now(), locked_at=None)


def close_sessions(user):
    """Borra las sesiones guardadas en la base de datos de `user` (sección 9).

    El mecanismo principal no es este: en cada petición Django vuelve a cargar la cuenta y rechaza
    la que tiene `is_active=False` (sirve con cualquier motor, también con Redis). Esto solo limpia
    las filas de `django_session` cuando el motor es la base de datos; con otro motor no hace nada.
    Solo se leen las sesiones vigentes. Devuelve cuántas borró.
    """
    if settings.SESSION_ENGINE != 'django.contrib.sessions.backends.db':
        return 0
    from django.contrib.sessions.models import Session
    wanted = str(user.pk)
    doomed = []
    for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
        try:
            data = session.get_decoded()
        except Exception:   # una sesión dañada no detiene la acción
            continue
        if str(data.get('_auth_user_id', '')) == wanted:
            doomed.append(session.session_key)
    if doomed:
        Session.objects.filter(session_key__in=doomed).delete()
    return len(doomed)


def reduce_webhook(payload):
    """El cuerpo de un webhook de Wompi reducido a la lista blanca (sin datos personales)."""
    if not isinstance(payload, dict):
        return None
    data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
    tx = data.get('transaction') if isinstance(data.get('transaction'), dict) else {}
    reduced = {'data': {'transaction': {key: tx[key] for key in WEBHOOK_TRANSACTION_WHITELIST if key in tx}}}
    if isinstance(payload.get('event'), str):
        reduced['event'] = payload['event']
    return reduced


def _lock_target(target):
    return User.objects.select_for_update().get(pk=target.pk)


def _reload_actor(actor):
    """Se relee quien actúa: pudo perder el permiso después de cargar la página."""
    if actor is None or not getattr(actor, 'pk', None):
        raise AccountNotAllowed('not_allowed')
    fresh = User.objects.filter(pk=actor.pk).first()
    if fresh is None:
        raise AccountNotAllowed('not_allowed')
    return fresh


def _audit(actor, target, action, reason='', reassigned_to=None, reassigned_count=0):
    return AccountAuditLog.objects.create(
        actor=actor, target=target, target_role=target.role, action=action, reason=reason,
        reassigned_to=reassigned_to, reassigned_count=reassigned_count,
    )


def _sync(target):
    """El objeto que tiene la vista queda con el estado guardado."""
    target.refresh_from_db()


# ─── Conteos para las confirmaciones (C-04, C-06 y C-07) ──────────────────────

def needs_payment_ack(client):
    """La clienta tiene una membresía vigente o un pago Wompi en curso (`ack_payments`)."""
    from apps.payments.models import Payment
    return bool(_membership_valid_until(client)) or Payment.objects.filter(
        user=client, status=Payment.STATUS_PENDING).exists()


def _membership_valid_until(client):
    from apps.memberships.models import Membership
    membership = Membership.objects.filter(user=client).first()
    if membership is not None and membership.is_valid:
        return membership.end_date
    return None


def erase_summary(target):
    """Conteos sin datos personales de lo que se borra y lo que se conserva."""
    from apps.assessments.models import BodyMeasurement, InitialAssessment
    from apps.health.models import HealthProfile
    from apps.payments.models import ManualPayment, Payment
    from apps.plans.models import Plan
    from apps.routines.models import Routine
    manual = ManualPayment.objects.filter(user=target)
    return {
        'health_versions': HealthProfile.objects.filter(user=target).count(),
        'assessments': InitialAssessment.objects.filter(user=target).count(),
        'measurements': BodyMeasurement.objects.filter(user=target).count(),
        'routines': Routine.objects.filter(user=target).count(),
        'plans': Plan.objects.filter(client=target).count(),
        'manual_payments': manual.count(),
        'receipts': manual.exclude(receipt='').exclude(receipt__isnull=True).count(),
        'wompi_payments': Payment.objects.filter(user=target).count(),
    }


def client_erase_context(client):
    from apps.payments.models import Payment
    return {
        'summary': erase_summary(client),
        'membership_valid_until': _membership_valid_until(client),
        'pending_wompi': Payment.objects.filter(user=client, status=Payment.STATUS_PENDING).exists(),
        'needs_ack': needs_payment_ack(client),
    }


def trainer_action_context(trainer):
    from apps.plans.models import Plan
    return {
        'client_count': User.objects.filter(assigned_trainer=trainer).count(),
        'in_review_count': Plan.objects.filter(status=Plan.STATUS_IN_REVIEW, reviewed_by=trainer).count(),
        'targets': list(valid_reassign_targets(trainer)),
    }


def validated_foods_hint(trainer):
    """¿Aparece su nombre como validador/a de algún alimento? (`Food.validated_by` es texto libre)."""
    from apps.plans.models import Food
    full_name = ' '.join((trainer.get_full_name() or '').split())
    if not full_name:
        return False
    return Food.objects.filter(validated_by__icontains=full_name).exists()


# ─── Desactivar ───────────────────────────────────────────────────────────────

def deactivate_account(target, actor, reason='', reassign_to=None):
    """Desactiva una cuenta (reversible). Idempotente: si ya estaba desactivada no cambia nada."""
    reason = clean_reason(reason, required=False)
    with transaction.atomic():
        actor = _reload_actor(actor)
        locked = _lock_target(target)
        check_can_act(actor, locked, choices.ACTION_DEACTIVATE)
        if not locked.is_active:
            return ActionResult(changed=False)
        result = ActionResult(changed=True)
        if locked.is_trainer:
            destination, count = _lock_destination(locked, reassign_to)
            if count:
                result.reassigned_count = reassign_clients(locked, destination)
                result.reassigned_to = destination
            result.released_count = release_in_review_plans(locked, actor)
        locked.is_active = False
        locked.deactivated_at = timezone.now()
        locked.save(update_fields=['is_active', 'deactivated_at'])
        cancel_whatsapp_queue(locked)
        if not locked.is_trainer:
            cancel_plan_jobs(locked)
        close_sessions(locked)
        _ensure_superuser_left()
        _audit(actor, locked, choices.ACTION_DEACTIVATE, reason, result.reassigned_to, result.reassigned_count)
    logger.info('account deactivated user_id=%s role=%s actor_id=%s reassigned=%s released=%s',
                locked.pk, locked.role, actor.pk, result.reassigned_count, result.released_count)
    _sync(target)
    return result


# ─── Reactivar ────────────────────────────────────────────────────────────────

def reactivate_account(target, actor):
    """Reactiva una cuenta desactivada (nunca una eliminada). Un entrenador NO recupera sus
    clientas. Idempotente."""
    with transaction.atomic():
        actor = _reload_actor(actor)
        locked = _lock_target(target)
        check_can_act(actor, locked, choices.ACTION_REACTIVATE)
        if locked.is_active:
            return ActionResult(changed=False)
        locked.is_active = True
        locked.deactivated_at = None
        locked.save(update_fields=['is_active', 'deactivated_at'])
        _audit(actor, locked, choices.ACTION_REACTIVATE)
    logger.info('account reactivated user_id=%s role=%s actor_id=%s', locked.pk, locked.role, actor.pk)
    _sync(target)
    return ActionResult(changed=True)


# ─── Eliminar definitivamente ─────────────────────────────────────────────────

def _model(label):
    from django.apps import apps
    app_label, model_name = label.split('.')
    return apps.get_model(app_label, model_name)


def _delete_owned_data(user, actor):
    """Borra las filas DELETE de `ERASE_POLICY`, en orden. La ficha de salud deja constancia en
    `HealthAccessLog` con quien elimina como actor."""
    from apps.health.models import HealthAccessLog, HealthProfile
    from apps.health.services import log_access
    versions = list(HealthProfile.objects.filter(user=user).values_list('version', flat=True))
    for key, policy in ERASE_POLICY.items():
        if policy != DELETE:
            continue
        app_label, model_name, field_name = key.split('.')
        _model(f'{app_label}.{model_name}').objects.filter(**{field_name: user}).delete()
    if versions:
        log_access(actor, user, HealthAccessLog.ACTION_DELETE, max(versions))


def _anonymize_kept_data(user, now):
    """Pagos y consentimientos se conservan, sin datos personales y apuntando a la lápida."""
    from apps.health.models import ConsentRecord
    from apps.payments.models import ManualPayment, Payment
    ManualPayment.objects.filter(user=user).update(notes='')
    # Respuesta 1a: el comprobante se conserva (bucket privado, solo la superusuaria) y se marca
    ManualPayment.objects.filter(user=user, receipt_retained_at__isnull=True).exclude(
        receipt='').exclude(receipt__isnull=True).update(receipt_retained_at=now)
    for payment in Payment.objects.filter(user=user).exclude(raw_webhook__isnull=True):
        payment.raw_webhook = reduce_webhook(payment.raw_webhook)
        payment.save(update_fields=['raw_webhook', 'updated_at'])
    ConsentRecord.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=now)


def _scrub_admin_log(user, membership_ids, manual_ids, payment_ids):
    """En el historial del admin, los objetos de la persona dejan de mostrar su nombre."""
    from django.contrib.admin.models import LogEntry
    from django.contrib.contenttypes.models import ContentType
    from apps.memberships.models import Membership
    from apps.payments.models import ManualPayment, Payment
    targets = [(User, [user.pk]), (Membership, membership_ids), (ManualPayment, manual_ids),
               (Payment, payment_ids)]
    for model, ids in targets:
        if not ids:
            continue
        LogEntry.objects.filter(
            content_type=ContentType.objects.get_for_model(model),
            object_id__in=[str(pk) for pk in ids],
        ).update(object_repr=ERASED_TEXT, change_message=ERASED_TEXT)


def _purge_axes(username):
    """Intentos de inicio de sesión con el usuario original (guardan IP y navegador)."""
    if not username or 'axes' not in settings.INSTALLED_APPS:
        return
    from axes.models import AccessAttempt, AccessFailureLog, AccessLog
    for model in (AccessAttempt, AccessLog, AccessFailureLog):
        model.objects.filter(username=username).delete()


def _make_tombstone(user, now):
    user.username = TOMBSTONE_USERNAME.format(pk=user.pk)
    user.first_name = user.last_name = user.email = user.phone = user.bio = ''
    user.gender = user.training_goal = ''
    user.interested_plan = None
    user.assigned_trainer = None
    user.profile_photo = None
    user.set_unusable_password()
    user.is_active = user.is_staff = user.is_superuser = False
    user.last_login = None
    user.deactivated_at = user.deactivated_at or now
    user.erased_at = now
    user.save()
    user.groups.clear()
    user.user_permissions.clear()


def _delete_files(user_pk, photo_name, storage):
    if not photo_name:
        return
    try:
        storage.delete(photo_name)
    except Exception:   # R2 caído: lo reintenta `purge_orphan_files`
        logger.warning('account erase file_delete_failed kind=photo user_id=%s', user_pk)


def erase_account(target, actor, reason, reassign_to=None):
    """Elimina definitivamente una cuenta y deja la lápida (sección 7)."""
    from apps.memberships.models import Membership
    from apps.payments.models import ManualPayment, Payment
    reason = clean_reason(reason, required=True)
    with transaction.atomic():
        actor = _reload_actor(actor)
        locked = _lock_target(target)
        check_can_act(actor, locked, choices.ACTION_ERASE)
        result = ActionResult(changed=True)
        if locked.is_trainer:
            destination, count = _lock_destination(locked, reassign_to)
            if count:
                result.reassigned_count = reassign_clients(locked, destination)
                result.reassigned_to = destination
            result.released_count = release_in_review_plans(locked, actor)
        now = timezone.now()
        original_username = locked.username
        photo_name = locked.profile_photo.name if locked.profile_photo else ''
        photo_storage = locked.profile_photo.storage if locked.profile_photo else None
        membership_ids = list(Membership.objects.filter(user=locked).values_list('pk', flat=True))
        manual_ids = list(ManualPayment.objects.filter(user=locked).values_list('pk', flat=True))
        payment_ids = list(Payment.objects.filter(user=locked).values_list('pk', flat=True))

        cancel_whatsapp_queue(locked)
        cancel_plan_jobs(locked)
        _delete_owned_data(locked, actor)
        _anonymize_kept_data(locked, now)
        _scrub_admin_log(locked, membership_ids, manual_ids, payment_ids)
        _purge_axes(original_username)
        close_sessions(locked)
        _make_tombstone(locked, now)
        _ensure_superuser_left()
        _audit(actor, locked, choices.ACTION_ERASE, reason, result.reassigned_to, result.reassigned_count)

        user_pk, role = locked.pk, locked.role

        def after_commit():
            _delete_files(user_pk, photo_name, photo_storage)
            logger.info('account erased user_id=%s role=%s', user_pk, role)
        transaction.on_commit(after_commit)
    _sync(target)
    return result


# ─── Transferir la administración ─────────────────────────────────────────────

def transfer_admin_candidates(actor):
    """Entrenadores a los que `actor` puede entregar la administración: activos, no eliminados,
    no superusuarios y distintos de quien entrega. Es la lista del formulario y la fuente de la
    regla de elegibilidad (la misma que `check_can_transfer_admin` aplica dentro de la transacción)."""
    return (User.objects.filter(role=User.ROLE_TRAINER, is_active=True, erased_at__isnull=True,
                                is_superuser=False)
            .exclude(pk=actor.pk).order_by('first_name', 'last_name', 'pk'))


def check_can_transfer_admin(actor, target):
    """Lanza `AccountNotAllowed` o `AccountFieldError` si `actor` no puede entregarle la
    administración a `target`. Quien entrega: entrenador + superusuaria + activa + no eliminada.
    Quien recibe: entrenador activo, no eliminado, no superusuario y distinto de quien entrega."""
    if not (actor is not None and getattr(actor, 'is_authenticated', False) and actor.is_trainer
            and actor.is_superuser and actor.is_active and not actor.is_erased):
        raise AccountNotAllowed('not_allowed')
    if target.pk == actor.pk:
        raise AccountNotAllowed('account_self')
    if target.is_superuser:
        raise AccountNotAllowed('account_protected')
    if not target.is_trainer:
        raise AccountNotAllowed('not_allowed')
    if target.is_erased or not target.is_active:
        # Lo normal es una carrera (se desactivó mientras la página estaba abierta): error del campo
        raise AccountFieldError('target_unavailable', 'new_admin', TRANSFER_TARGET_MESSAGE)


def transfer_admin(actor, target, password):
    """La superusuaria `actor` le entrega la administración al entrenador `target` (y deja de serlo).

    Una sola transacción con `select_for_update()` sobre las DOS filas (en orden de id, para que dos
    traspasos cruzados no se bloqueen entre sí). Comprueba las reglas con las filas ya bloqueadas, así
    que un doble clic o una carrera no deja a nadie sin permiso ni con dos superusuarias.

    - `password` es la contraseña de quien entrega, escrita de nuevo (reautenticación); si no
      coincide, `AccountFieldError('password_invalid')` con un mensaje genérico y no cambia nada.
      El límite de intentos lo pone la vista (`ratelimit.hit`), porque es una regla de HTTP.
    - `target` pasa a `is_superuser=True, is_staff=True`. `actor` queda como un entrenador común:
      `is_superuser=False` e `is_staff=True` (así los crean `create_trainer` y «Agregar entrenador»).
    - No se reasigna ninguna clienta: `actor` conserva las suyas (`assigned_clients`).
    - Sesiones: no se toca ninguna. Django vuelve a cargar la cuenta en cada petición, así que el
      cambio rige de inmediato en todas las sesiones abiertas de las dos personas, y `is_superuser`
      no forma parte del hash de la sesión (solo la contraseña), así que nadie queda desconectada.
      Para cortar el acceso de la persona anterior, la nueva administradora la desactiva desde
      «Entrenadores» (eso sí cierra sus sesiones).
    - Auditoría: `AccountAuditLog` con `action='transfer_admin'` (actor = quien entrega, target =
      quien recibe). El log solo lleva ids.
    """
    if not getattr(actor, 'pk', None) or not getattr(target, 'pk', None):
        raise AccountNotAllowed('not_allowed')
    with transaction.atomic():
        rows = {row.pk: row for row in
                User.objects.select_for_update().filter(pk__in={actor.pk, target.pk}).order_by('pk')}
        locked_actor, locked_target = rows.get(actor.pk), rows.get(target.pk)
        if locked_actor is None or locked_target is None:
            raise AccountNotAllowed('not_allowed')
        check_can_transfer_admin(locked_actor, locked_target)
        if not password or not locked_actor.check_password(password):
            logger.warning('admin transfer rejected code=password_invalid actor_id=%s', locked_actor.pk)
            raise AccountFieldError('password_invalid', 'password', TRANSFER_PASSWORD_MESSAGE)
        locked_target.is_superuser = True
        locked_target.is_staff = True
        locked_target.save(update_fields=['is_superuser', 'is_staff'])
        locked_actor.is_superuser = False
        locked_actor.is_staff = True
        locked_actor.save(update_fields=['is_superuser', 'is_staff'])
        _ensure_superuser_left()
        _audit(locked_actor, locked_target, choices.ACTION_TRANSFER_ADMIN)
    logger.info('admin transferred actor_id=%s target_id=%s', locked_actor.pk, locked_target.pk)
    _sync(actor)
    _sync(target)
    return ActionResult(changed=True)
