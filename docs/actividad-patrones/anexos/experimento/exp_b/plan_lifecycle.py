"""Ciclo de vida de un `Plan` (cambios de estado), modelado con el patrón State (GoF).

Capas (Clean Architecture; los controladores HTTP quedan fuera y solo llaman a las funciones públicas):

- Servicios de negocio (abajo del todo): `take_for_review`, `archive_plan`, `approve_plan` y
  `regenerate_plan`, que comprueban el acceso del entrenador y delegan en el contexto, y
  `expire_overdue_plans`, que corre sin entrenador (proceso programado) y marca como vencidos
  los planes aprobados cuya vigencia ya pasó.
- Dominio: `PlanLifecycle` (el contexto) delega cada operación en la clase del estado actual del
  plan. Cada estado (`DraftAIState`, `InReviewState`, `ApprovedState`, `ExpiredState`,
  `SupersededState`, `ArchivedState`) declara qué operaciones admite y a qué estado pasa; lo que
  no admite lo rechaza la clase base `PlanState`. No hay `if plan.status ...` en las operaciones
  públicas.
- Acceso a datos: `PlanRepository` (consultas, bloqueos de fila, rutina y eventos).

Quedan fuera (las comprueba quien llama): la credencial de nutrición, que la rutina tenga
ejercicios y la ficha desactualizada. Los mensajes de error son genéricos y el registro
(`logger`) solo lleva ids y estados, nunca datos de la clienta.
"""
import logging
from contextlib import contextmanager
from datetime import timedelta
from functools import partial

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.permissions import clients_for_trainer
from apps.health import dates as health_dates
from apps.plans import notifications, services
from apps.plans.models import Plan, PlanEvent
from apps.plans.services import (
    ARCHIVE_REASON_MAX, NOTE_FOR_CLIENT_MAX, PLAN_VALID_DAYS, TRAINER_NOTES_MAX, PlanNotAllowed, PlanStateError,
)

logger = logging.getLogger(__name__)

MSG_NO_ACCESS = 'No tienes acceso a este plan.'
MSG_CANNOT_ARCHIVE = 'Este plan ya no se puede archivar.'
MSG_CANNOT_APPROVE = 'Solo se puede aprobar un borrador o un plan en revisión.'
MSG_SIMULATED = 'Este plan se generó en MODO DE PRUEBA: no se puede aprobar. Genera uno real.'
MSG_WARNINGS = 'Marca "Revisé las advertencias" para aprobar.'
MSG_CHANGED = 'Este plan cambió mientras lo revisabas. Recarga la página.'
MSG_CANNOT_REGENERATE = 'Solo se regenera un borrador o un plan en revisión.'
MSG_UNKNOWN_STATE = 'Este plan está en un estado desconocido.'
DEFAULT_ARCHIVE_NOTE = 'Archivado por el entrenador.'
EXPIRED_NOTE = 'Vigencia vencida.'

# El modelo no define el estado «vencido» ni su acción; ambas cadenas caben en sus campos.
STATUS_EXPIRED = 'expired'
ACTION_EXPIRED = 'expired'


def _clip(text, limit):
    return (text or '').strip()[:limit]


# ─── Acceso a datos ───────────────────────────────────────────────────────────

class PlanRepository:
    """Única clase que toca la base de datos en este módulo."""

    def trainer_can_access(self, actor, plan):
        if not getattr(actor, 'is_trainer', False):
            return False
        return clients_for_trainer(actor).filter(pk=plan.client_id).exists()

    def lock(self, plan):
        """Bloquea la fila del plan (dentro de una transacción) y deja `plan` con lo que hay en la base."""
        Plan.objects.select_for_update().get(pk=plan.pk)
        plan.refresh_from_db()

    def approved_siblings(self, plan):
        """Planes aprobados de la misma clienta y tipo (distintos de `plan`), bloqueados."""
        return list(
            Plan.objects.select_for_update()
            .filter(client_id=plan.client_id, kind=plan.kind, status=Plan.STATUS_APPROVED)
            .exclude(pk=plan.pk)
        )

    def overdue_approved(self, today):
        """Planes aprobados cuya vigencia terminó antes de `today` (sin bloquear)."""
        return list(Plan.objects.filter(status=Plan.STATUS_APPROVED, valid_until__lt=today).order_by('pk'))

    def save(self, plan, fields):
        plan.save(update_fields=fields)

    def set_routine_active(self, plan, active):
        routine = plan.routine
        if routine is None:
            return
        routine.is_active = active
        routine.save(update_fields=['is_active', 'updated_at'])

    def add_event(self, plan, actor, action, note=''):
        return PlanEvent.objects.create(plan=plan, actor=actor, action=action, note=note)


# ─── Estados (patrón State) ───────────────────────────────────────────────────

class PlanState:
    """Estado base: no admite ninguna operación. Los estados concretos sobrescriben solo las suyas."""
    status = None
    entered_action = None     # evento que se registra al entrar en este estado
    routine_active = None     # None = no toca la rutina; True/False = la activa/desactiva al entrar

    def take_for_review(self, ctx, actor):
        return False

    def archive(self, ctx, actor, reason):
        raise PlanStateError(MSG_CANNOT_ARCHIVE)

    def approve(self, ctx, actor, *, note_for_client, warnings_ack):
        raise PlanStateError(MSG_CANNOT_APPROVE)

    def ensure_approvable_under_lock(self):
        """Segunda verificación, ya con la fila bloqueada: solo un estado pendiente la supera."""
        raise PlanStateError(MSG_CHANGED)

    def regenerate(self, ctx, actor, notes, request_token):
        raise PlanStateError(MSG_CANNOT_REGENERATE)

    def supersede(self, ctx, actor, replacement):
        raise PlanStateError(MSG_CHANGED)

    def expire(self, ctx, actor):
        """Solo un plan aprobado vence; en cualquier otro estado no hace nada (idempotente)."""
        return False


class _ArchivableState(PlanState):
    """Estados desde los que se puede archivar: pasan a `archived`."""

    def archive(self, ctx, actor, reason):
        with ctx.locked():
            ctx.ensure_state(self, MSG_CANNOT_ARCHIVE)
            ctx.transition(
                Plan.STATUS_ARCHIVED, actor,
                note=_clip(reason, ARCHIVE_REASON_MAX) or DEFAULT_ARCHIVE_NOTE,
                archived_at=timezone.now(),
            )
        return ctx.plan


class _PendingState(_ArchivableState):
    """Borrador o plan en revisión: se puede aprobar, regenerar y archivar."""

    def ensure_approvable_under_lock(self):
        return None

    def approve(self, ctx, actor, *, note_for_client, warnings_ack):
        plan = ctx.plan
        if plan.is_simulated and not settings.DEBUG:
            raise PlanStateError(MSG_SIMULATED)
        if plan.flags and not warnings_ack:
            raise PlanStateError(MSG_WARNINGS)
        with ctx.locked():
            ctx.state.ensure_approvable_under_lock()
            ctx.supersede_previous(actor)
            now = timezone.now()
            fields = {
                'approved_by': actor,
                'approved_at': now,
                'reviewed_by': plan.reviewed_by if plan.reviewed_by_id else actor,
                'note_for_client': _clip(note_for_client, NOTE_FOR_CLIENT_MAX),
                'valid_until': health_dates.today() + timedelta(days=PLAN_VALID_DAYS),
            }
            if plan.flags:
                fields.update(warnings_ack_by=actor, warnings_ack_at=now)
            ctx.transition(Plan.STATUS_APPROVED, actor, **fields)
            transaction.on_commit(partial(_notify_client, plan))
        return plan

    def regenerate(self, ctx, actor, notes, request_token):
        plan = ctx.plan
        with transaction.atomic():
            job, created = services.request_plan(
                plan.client, plan.kind, actor, request_token, trainer_notes=notes, parent_plan=plan)
            if created:
                ctx.record(actor, PlanEvent.ACTION_REGENERATE, _clip(notes, TRAINER_NOTES_MAX))
        return job, created


class DraftAIState(_PendingState):
    status = Plan.STATUS_DRAFT_AI
    entered_action = PlanEvent.ACTION_CREATED

    def take_for_review(self, ctx, actor):
        with ctx.locked():
            if ctx.state is not self:
                return False
            ctx.transition(Plan.STATUS_IN_REVIEW, actor, reviewed_by=actor)
        return True


class InReviewState(_PendingState):
    status = Plan.STATUS_IN_REVIEW
    entered_action = PlanEvent.ACTION_TAKEN


class ApprovedState(_ArchivableState):
    status = Plan.STATUS_APPROVED
    entered_action = PlanEvent.ACTION_APPROVED
    routine_active = True

    def supersede(self, ctx, actor, replacement):
        ctx.transition(
            Plan.STATUS_SUPERSEDED, actor, note=f'Reemplazado por la versión {replacement.version}.')

    def expire(self, ctx, actor):
        with ctx.locked():
            if ctx.state is not self:
                return False
            ctx.transition(STATUS_EXPIRED, actor, note=EXPIRED_NOTE)
        return True


class ExpiredState(_ArchivableState):
    """Plan aprobado cuya vigencia terminó: solo se puede archivar (la rutina ya está inactiva)."""
    status = STATUS_EXPIRED
    entered_action = ACTION_EXPIRED
    routine_active = False


class SupersededState(PlanState):
    status = Plan.STATUS_SUPERSEDED
    entered_action = PlanEvent.ACTION_SUPERSEDED
    routine_active = False


class ArchivedState(PlanState):
    status = Plan.STATUS_ARCHIVED
    entered_action = PlanEvent.ACTION_ARCHIVED
    routine_active = False


_STATES = {
    state.status: state
    for state in (
        DraftAIState(), InReviewState(), ApprovedState(), ExpiredState(), SupersededState(), ArchivedState())
}


def state_for(status):
    try:
        return _STATES[status]
    except KeyError:
        raise PlanStateError(MSG_UNKNOWN_STATE) from None


# ─── Contexto ─────────────────────────────────────────────────────────────────

class PlanLifecycle:
    """Contexto del patrón State: el plan y su repositorio. Cada operación se delega en el estado actual."""

    def __init__(self, plan, repository=None):
        self.plan = plan
        self.repository = repository or PlanRepository()

    @property
    def state(self):
        return state_for(self.plan.status)

    # Operaciones: las resuelve el estado actual
    def take_for_review(self, actor):
        return self.state.take_for_review(self, actor)

    def archive(self, actor, reason=''):
        return self.state.archive(self, actor, reason)

    def approve(self, actor, *, note_for_client='', warnings_ack=False):
        return self.state.approve(self, actor, note_for_client=note_for_client, warnings_ack=warnings_ack)

    def regenerate(self, actor, notes, request_token):
        return self.state.regenerate(self, actor, notes, request_token)

    def supersede(self, actor, replacement):
        return self.state.supersede(self, actor, replacement)

    def expire(self, actor=None):
        return self.state.expire(self, actor)

    # Apoyo para los estados
    @contextmanager
    def locked(self):
        """Transacción con la fila del plan bloqueada y `plan` recargado de la base."""
        with transaction.atomic():
            self.repository.lock(self.plan)
            yield

    def ensure_state(self, expected, message):
        if self.state is not expected:
            raise PlanStateError(message)

    def transition(self, target_status, actor, *, note='', **fields):
        """Pasa el plan a `target_status`, guarda `fields`, ajusta la rutina y registra el evento."""
        plan = self.plan
        origin = plan.status
        target = state_for(target_status)
        plan.status = target.status
        for name, value in fields.items():
            setattr(plan, name, value)
        self.repository.save(plan, ['status', *fields])
        if target.routine_active is not None:
            self.repository.set_routine_active(plan, target.routine_active)
        self.repository.add_event(plan, actor, target.entered_action, note)
        logger.info('Plan %s: %s -> %s (usuario %s).', plan.pk, origin, target.status, getattr(actor, 'pk', None))

    def record(self, actor, action, note=''):
        self.repository.add_event(self.plan, actor, action, note)
        logger.info('Plan %s: evento %s (usuario %s).', self.plan.pk, action, getattr(actor, 'pk', None))

    def supersede_previous(self, actor):
        """Reemplaza al plan aprobado anterior de la misma clienta y tipo (si lo hay)."""
        for previous in self.repository.approved_siblings(self.plan):
            PlanLifecycle(previous, self.repository).supersede(actor, self.plan)


def _notify_client(plan):
    try:
        notifications.client_plan_approved(plan)
    except Exception:   # el aviso nunca debe romper una aprobación ya confirmada
        logger.warning('No se pudo avisar a la clienta del plan %s.', plan.pk)


# ─── Servicios de negocio (API pública) ───────────────────────────────────────

def _lifecycle_for(plan, actor):
    context = PlanLifecycle(plan)
    if not context.repository.trainer_can_access(actor, plan):
        logger.warning('Acceso denegado al plan %s (usuario %s).', plan.pk, getattr(actor, 'pk', None))
        raise PlanNotAllowed(MSG_NO_ACCESS)
    return context


def take_for_review(plan, actor):
    """`draft_ai` -> `in_review`. True si cambió el estado; False si el plan no estaba en borrador."""
    return _lifecycle_for(plan, actor).take_for_review(actor)


def archive_plan(plan, actor, reason=''):
    """Archiva un borrador, un plan en revisión, uno aprobado o uno vencido."""
    return _lifecycle_for(plan, actor).archive(actor, reason)


def approve_plan(plan, actor, *, note_for_client='', warnings_ack=False, stale_ack=False):
    """Aprueba un borrador o un plan en revisión (`stale_ack` se acepta, pero este módulo no lo usa)."""
    return _lifecycle_for(plan, actor).approve(actor, note_for_client=note_for_client, warnings_ack=warnings_ack)


def regenerate_plan(plan, actor, notes, request_token):
    """Pide un trabajo de generación a partir de un borrador o un plan en revisión: `(job, created)`."""
    return _lifecycle_for(plan, actor).regenerate(actor, notes, request_token)


def expire_overdue_plans(today=None):
    """Pasa a `expired` los planes `approved` con `valid_until` anterior a `today` (por defecto, hoy).

    Desactiva su rutina y registra el evento `expired` de cada uno (sin autor: lo hace el sistema).
    Devuelve cuántos cambió. Es idempotente: los planes que ya no están aprobados no se tocan.
    """
    if today is None:
        today = health_dates.today()
    repository = PlanRepository()
    expired = sum(1 for plan in repository.overdue_approved(today) if PlanLifecycle(plan, repository).expire())
    logger.info('Planes vencidos al %s: %s.', today, expired)
    return expired


__all__ = [
    'PlanLifecycle', 'PlanRepository', 'PlanState', 'state_for',
    'take_for_review', 'archive_plan', 'approve_plan', 'regenerate_plan', 'expire_overdue_plans',
]
