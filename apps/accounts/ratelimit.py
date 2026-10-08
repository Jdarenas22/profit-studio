"""Límite de frecuencia simple (ventana fija) sobre la caché de Django.

Usa la caché configurada: Redis si existe REDIS_URL (límite compartido entre todos
los procesos) o LocMem (cada proceso de gunicorn cuenta por separado, así que el
límite real puede ser hasta N veces mayor con N procesos; sigue frenando abusos
masivos). Si la caché falla, NO se bloquea a nadie (falla abierta) y se registra.
"""
import hashlib
import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)


def hit(scope, identifier, limit, window_seconds):
    """Cuenta un evento y devuelve True si todavía está dentro del límite."""
    digest = hashlib.sha256(str(identifier).encode()).hexdigest()[:32]
    key = f'ratelimit:{scope}:{digest}'
    try:
        if cache.add(key, 1, window_seconds):
            return 1 <= limit
        try:
            count = cache.incr(key)
        except ValueError:      # la clave venció entre add() e incr()
            cache.set(key, 1, window_seconds)
            count = 1
        return count <= limit
    except Exception:           # caché caída: no se bloquea a nadie
        logger.exception('Rate limit: la caché falló (scope=%s); se permite la petición.', scope)
        return True
