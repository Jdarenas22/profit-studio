"""Reglas de autorización por cliente (evita el acceso a datos de clientes ajenos).

Regla única de la plataforma (asignación automática):
  - Superusuaria: ve y gestiona a TODOS los clientes (y es la única que los asigna).
  - Entrenador (no superusuario): ve y gestiona SOLO a los clientes cuyo
    `assigned_trainer` es él. Los clientes que se auto-registran quedan sin
    asignar y solo los ve la superusuaria hasta que los asigna. Los clientes que
    crea un entrenador desde "Agregar cliente" quedan asignados a ese entrenador.
  - Cualquier otro usuario (cliente, anónimo): no accede a ninguno.

Cuando el recurso no es accesible se responde 404 (no se revela si existe).
Toda vista que reciba el pk de un cliente, o de algo que pertenece a un cliente
(valoración, rutina, membresía, medición, pago manual), debe pasar por aquí.

Cuentas (docs/contracts/cuentas-eliminar.md): las cuentas ELIMINADAS (lápidas) no aparecen nunca;
las DESACTIVADAS sí (para verlas y reactivarlas), pero su ficha es de solo lectura: las vistas y
los servicios que escriben datos de una clienta llaman a `ensure_client_active`.
"""
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect

from .models import User, pending_registration_q


def clients_for_trainer(user):
    """Queryset de clientes (role='member') a los que `user` puede acceder."""
    # Las cuentas registradas que todavía no confirmaron su correo (inactivas y sin verificar) no son
    # clientas para el panel: no se listan ni se pueden abrir hasta que confirmen
    # (docs/contracts/verificacion-correo.md, sección 6). La invitación pendiente SÍ se ve.
    clients = User.objects.filter(role=User.ROLE_MEMBER, erased_at__isnull=True).exclude(
        pending_registration_q())
    if not getattr(user, 'is_authenticated', False) or not user.is_trainer:
        return clients.none()
    if user.is_superuser:
        return clients
    return clients.filter(assigned_trainer=user)


def get_client_for_trainer(request, pk):
    """Devuelve el cliente `pk` si el usuario de la petición puede acceder a él.

    Lanza Http404 si no existe, no es un cliente o pertenece a otro entrenador.
    """
    return get_object_or_404(clients_for_trainer(request.user), pk=pk)


def ensure_client_access(request, client):
    """Verifica que `client` (ya cargado, p. ej. assessment.user) sea accesible."""
    if not clients_for_trainer(request.user).filter(pk=client.pk).exists():
        raise Http404
    return client


INACTIVE_CLIENT_MESSAGE = 'Esta cuenta está desactivada. Reactívala para hacer cambios.'


class ClientInactiveError(Exception):
    """Se intentó escribir datos de una clienta desactivada o eliminada."""

    def __init__(self, message=INACTIVE_CLIENT_MESSAGE):
        super().__init__(message)


def ensure_client_active(client):
    """Regla de solo lectura (respuesta 4a): nadie escribe datos de una clienta desactivada.

    La lanzan los servicios (defensa en profundidad) y la usan las vistas con
    `reject_if_inactive`. Se relee la fila: la cuenta pudo desactivarse después de cargarla.
    """
    state = User.objects.filter(pk=client.pk).values('is_active', 'erased_at').first()
    if state is None or not state['is_active'] or state['erased_at'] is not None:
        raise ClientInactiveError()
    return client


def reject_if_inactive(request, client):
    """Para las vistas que escriben: `None` si la clienta está activa; si no, la redirección al
    detalle con el mensaje de solo lectura."""
    try:
        ensure_client_active(client)
    except ClientInactiveError as exc:
        messages.error(request, str(exc))
        return redirect('trainer_client_detail', pk=client.pk)
    return None
