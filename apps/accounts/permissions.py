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
"""
from django.http import Http404
from django.shortcuts import get_object_or_404

from .models import User


def clients_for_trainer(user):
    """Queryset de clientes (role='member') a los que `user` puede acceder."""
    clients = User.objects.filter(role=User.ROLE_MEMBER)
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
