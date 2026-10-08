"""Servir /media/ sin exponer los archivos privados (comprobantes de pago).

La ruta /media/ solo existe cuando NO hay almacenamiento externo (R2): en desarrollo o en
producción sin R2. Los comprobantes (`receipts/`) se entregan únicamente por la vista
protegida `payment_receipt` (payments/receipts/<pk>/), que revisa quién pide el
archivo. Esta ruta pública responde 404 a todo lo que cuelgue de `receipts/`.
"""
import posixpath

from django.http import Http404
from django.views.static import serve

# Prefijos (primer segmento de la ruta) que nunca se sirven por la ruta pública.
PRIVATE_MEDIA_PREFIXES = ('receipts',)


def is_private_media_path(path):
    """True si `path` (relativo a /media/) cae dentro de un prefijo privado.

    Se normaliza igual que lo hace el sistema de archivos para que ninguna variante
    ("./receipts/x", "a/../receipts/x", "RECEIPTS/x" en Windows, "receipts./x", "\\receipts\\x")
    esquive el bloqueo.
    """
    normalized = posixpath.normpath(str(path or '').replace('\\', '/')).lstrip('/')
    if normalized in ('', '.'):
        return False
    first = normalized.split('/', 1)[0]
    if first == '..':
        return True   # intento de salir de MEDIA_ROOT: lo rechaza también `serve`, pero aquí ni se intenta
    # En Windows "receipts." y "receipts " apuntan a la misma carpeta que "receipts"
    return first.rstrip('. ').lower() in PRIVATE_MEDIA_PREFIXES


def serve_public_media(request, path, document_root=None):
    """`django.views.static.serve` que responde 404 a los prefijos privados."""
    if is_private_media_path(path):
        raise Http404('No encontrado.')
    return serve(request, path, document_root=document_root)
