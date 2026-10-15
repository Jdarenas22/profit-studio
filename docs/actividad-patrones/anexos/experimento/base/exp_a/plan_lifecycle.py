"""Ciclo de vida de un `Plan`: los cambios de estado (capa de casos de uso).

Estados: `draft_ai` -> `in_review` -> `approved` -> `superseded` / `archived`.

Capas de este módulo (de adentro hacia afuera):

1. Reglas puras: constantes y mensajes. No tocan la base de datos.
2. Acceso a datos: funciones pequeñas con prefijo `_` que leen o escriben `Plan`, `PlanEvent` y
   `Routine`. Los casos de uso no escriben consultas sueltas.
3. Autorización: `_ensure_access` (el actor debe ser entrenador con acceso a la clienta).
4. Casos de uso públicos: `take_for_review`, `archive_plan`, `approve_plan`, `regenerate_plan`.

Las vistas HTTP (controladores) solo traducen la petición a estas llamadas y las excepciones a
respuestas. Aquí no hay `request` ni plantillas. Quedan fuera, porque las comprueba quien llama:
la credencial de nutrición, que la rutina tenga ejercicios y la ficha desactualizada.

Los mensajes de error son genéricos (no revelan si el plan existe ni datos de la clienta) y el
`logger` solo registra ids y estados: nunca notas, motivos ni datos de salud.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.permissions import clients_for_trainer
from apps.health import dates
from apps.plans import notifications, services
from apps.plans.models import Plan, PlanEvent
from apps.plans.services import PlanNotAllowed, PlanStateError
from apps.routines.models import Routine

__all__ = [
    'PlanNotAllowed', 'PlanStateError',
    'take_for_review', 'archive_plan', 'approve_plan', 'regenerate_plan',
]

logger = logging.getLogger(__name__)

# ─── 1. Reglas puras ──────────────────────────────────────────────────────────

ARCHIVABLE_STATUSES = (Plan.STATUS_DRAFT_AI, Plan.STATUS_IN_REVIEW, Plan.STATUS_APPROVED)
APPROVAL_VALIDITY_DAYS = 28
ARCHIVE_REASON_MAX = 300
NOTE_MAX = 500
DEFAULT_ARCHIVE_REASON = 'Archivado por el entrenador.'

MSG_NOT_ALLOWED = 'No tienes acceso a este plan.'
MSG_CANNOT_ARCHIVE = 'Este plan ya no se puede archivar.'
MSG_CANNOT_APPROVE = 'Solo se puede aprobar un borrador o un plan en revisión.'
MSG_SIMULATED = 'Este plan se generó en MODO DE PRUEBA: no se puede aprobar. Genera uno real.'
MSG_ACK_WARNINGS = 'Marca "Revisé las advertencias" para aprobar.'
MSG_CHANGED = 'Este plan cambió mientras lo revisabas. Recarga la página.'
MSG_CANNOT_REGENERATE = 'Solo se regenera un borrador o un plan en revisión.'


def _clip(text, limit):
    """Texto sin espacios en los bordes y recortado a `limit` caracteres (acepta None)."""
    return (text or '').strip()[:limit]


# ─── 2. Acceso a datos ────────────────────────────────────────────────────────

def _lock_plan(plan_id):
    """Lee el plan bloqueando su fila hasta el final de la transacción en curso."""
    return Plan.objects.select_for_update().get(pk=plan_id)


def _stored_status(plan):
    """Estado guardado en la base (el objeto en memoria puede estar desactualizado)."""
    return Plan.objects.filter(pk=plan.pk).values_list('status', flat=True).first()


def _record_event(plan, actor, action, note=''):
    return PlanEvent.objects.create(plan=plan, actor=actor, action=action, note=note)


def _set_routine_active(plan, active):
    """Activa o desactiva la rutina del plan (las vistas de la clienta solo ven las activas)."""
    if plan.routine_id:
        Routine.objects.filter(pk=plan.routine_id).update(is_active=active, updated_at=timezone.now())


def _supersede_current_approved(new_plan, actor):
    """El plan aprobado anterior de la misma clienta y tipo pasa a `superseded`.

    Debe ejecutarse antes de aprobar el nuevo: la base solo admite un aprobado por clienta y tipo.
    """
    previous = (
        Plan.objects.select_for_update()
        .filter(client_id=new_plan.client_id, kind=new_plan.kind, status=Plan.STATUS_APPROVED)
        .exclude(pk=new_plan.pk)
    )
    for old in list(previous):
        old.status = Plan.STATUS_SUPERSEDED
        old.save(update_fields=['status'])
        _set_routine_active(old, False)
        _record_event(old, actor, PlanEvent.ACTION_SUPERSEDED, f'Reemplazado por la versión {new_plan.version}.')
        logger.info('Plan %s: approved -> superseded por el plan %s.', old.pk, new_plan.pk)


def _notify_client_approved(plan):
    """Aviso a la clienta tras confirmarse la transacción. Un fallo del aviso no deshace la aprobación."""
    try:
        notifications.client_plan_approved(plan)
    except Exception:  # noqa: BLE001 - el aviso es secundario; se registra y se sigue
        logger.exception('No se pudo avisar a la clienta de la aprobación del plan %s.', plan.pk)


# ─── 3. Autorización ──────────────────────────────────────────────────────────

def _ensure_access(plan, actor):
    """El actor debe ser entrenador con acceso a la clienta del plan."""
    allowed = bool(getattr(actor, 'is_trainer', False)) and (
        clients_for_trainer(actor).filter(pk=plan.client_id).exists()
    )
    if not allowed:
        logger.warning('Acceso denegado al plan %s (actor %s).', plan.pk, getattr(actor, 'pk', None))
        raise PlanNotAllowed(MSG_NOT_ALLOWED)


# ─── 4. Casos de uso ──────────────────────────────────────────────────────────

def take_for_review(plan, actor):
    """`draft_ai` -> `in_review`. Devuelve True si cambió el estado, False si no estaba en borrador.

    Es una actualización condicional: si dos entrenadores lo toman a la vez, solo uno gana.
    """
    _ensure_access(plan, actor)
    with transaction.atomic():
        changed = Plan.objects.filter(pk=plan.pk, status=Plan.STATUS_DRAFT_AI).update(
            status=Plan.STATUS_IN_REVIEW, reviewed_by=actor,
        )
        if not changed:
            return False
        _record_event(plan, actor, PlanEvent.ACTION_TAKEN)
    plan.status = Plan.STATUS_IN_REVIEW
    plan.reviewed_by = actor
    logger.info('Plan %s: draft_ai -> in_review (actor %s).', plan.pk, actor.pk)
    return True


def archive_plan(plan, actor, reason=''):
    """Archiva un borrador, un plan en revisión o un plan aprobado y desactiva su rutina."""
    _ensure_access(plan, actor)
    with transaction.atomic():
        locked = _lock_plan(plan.pk)
        if locked.status not in ARCHIVABLE_STATUSES:
            raise PlanStateError(MSG_CANNOT_ARCHIVE)
        previous_status = locked.status
        locked.status = Plan.STATUS_ARCHIVED
        locked.archived_at = timezone.now()
        locked.save(update_fields=['status', 'archived_at'])
        _set_routine_active(locked, False)
        _record_event(
            locked, actor, PlanEvent.ACTION_ARCHIVED, _clip(reason, ARCHIVE_REASON_MAX) or DEFAULT_ARCHIVE_REASON,
        )
    plan.refresh_from_db()
    logger.info('Plan %s: %s -> archived (actor %s).', plan.pk, previous_status, actor.pk)
    return plan


def approve_plan(plan, actor, *, note_for_client='', warnings_ack=False, stale_ack=False):
    """`draft_ai` o `in_review` -> `approved`; el aprobado anterior de la clienta pasa a `superseded`.

    `stale_ack` se acepta por compatibilidad con quien llama, pero este módulo no lo usa.
    """
    _ensure_access(plan, actor)
    if not plan.is_pending:
        raise PlanStateError(MSG_CANNOT_APPROVE)
    if plan.is_simulated and not settings.DEBUG:
        raise PlanStateError(MSG_SIMULATED)
    if plan.flags and not warnings_ack:
        raise PlanStateError(MSG_ACK_WARNINGS)

    with transaction.atomic():
        locked = _lock_plan(plan.pk)
        if not locked.is_pending:
            raise PlanStateError(MSG_CHANGED)
        now = timezone.now()
        previous_status = locked.status
        _supersede_current_approved(locked, actor)

        locked.status = Plan.STATUS_APPROVED
        locked.approved_by = actor
        locked.approved_at = now
        if not locked.reviewed_by_id:
            locked.reviewed_by = actor
        locked.note_for_client = _clip(note_for_client, NOTE_MAX)
        locked.valid_until = dates.today() + timedelta(days=APPROVAL_VALIDITY_DAYS)
        update_fields = [
            'status', 'approved_by', 'approved_at', 'reviewed_by', 'note_for_client', 'valid_until',
        ]
        if locked.flags:
            locked.warnings_ack_by = actor
            locked.warnings_ack_at = now
            update_fields += ['warnings_ack_by', 'warnings_ack_at']
        locked.save(update_fields=update_fields)

        _set_routine_active(locked, True)
        _record_event(locked, actor, PlanEvent.ACTION_APPROVED)
        transaction.on_commit(lambda: _notify_client_approved(locked))
    plan.refresh_from_db()
    logger.info('Plan %s: %s -> approved (actor %s).', plan.pk, previous_status, actor.pk)
    return plan


def regenerate_plan(plan, actor, notes, request_token):
    """Pide una versión nueva de un borrador o de un plan en revisión. Devuelve `(job, created)`.

    `request_token` hace la operación idempotente: repetir el pedido devuelve el mismo trabajo
    con `created=False` y no registra un segundo evento.
    """
    _ensure_access(plan, actor)
    if _stored_status(plan) not in Plan.PENDING_STATUSES:
        raise PlanStateError(MSG_CANNOT_REGENERATE)
    notes = notes or ''
    with transaction.atomic():
        job, created = services.request_plan(
            plan.client, plan.kind, actor, request_token, trainer_notes=notes, parent_plan=plan,
        )
        if created:
            _record_event(plan, actor, PlanEvent.ACTION_REGENERATE, _clip(notes, NOTE_MAX))
    if created:
        logger.info('Plan %s: regeneración pedida (actor %s).', plan.pk, actor.pk)
    return job, created
