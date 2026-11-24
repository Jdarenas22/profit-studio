"""Destino de regreso (`next`) de las pantallas de salud de la clienta.

Una sola regla para el consentimiento, la ficha y las medidas: `next` solo se acepta si es una ruta
INTERNA (evita la redirección abierta). Lo usa el camino guiado (docs/contracts/camino-guiado.md)
para devolver a la clienta a donde empezó.
"""
from urllib.parse import quote

from django.utils.http import url_has_allowed_host_and_scheme

NEXT_MAX_LENGTH = 500


def is_safe_next(request, target):
    """¿`target` es una ruta interna? Empieza con una sola `/`, sin espacios ni caracteres de control
    (Django solo revisa estos últimos al inicio; uno en medio rompería la cabecera `Location`), mismo
    host y esquema permitido."""
    return bool(
        target
        and len(target) <= NEXT_MAX_LENGTH
        and target.startswith('/')
        and not target.startswith('//')
        and all(char.isprintable() and not char.isspace() for char in target)
        and url_has_allowed_host_and_scheme(
            target, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    )


def safe_next(request):
    """`next` del POST (o de la URL) si es una ruta interna; si no, `None`."""
    target = request.POST.get('next') or request.GET.get('next') or ''
    return target if is_safe_next(request, target) else None


def with_next(url, next_url):
    """`url` con `?next=` (o `&next=`) hacia `next_url`. Sin `next_url` devuelve `url` igual."""
    if not next_url:
        return url
    return f"{url}{'&' if '?' in url else '?'}next={quote(next_url, safe='/')}"
