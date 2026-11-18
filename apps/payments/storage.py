"""Almacenamiento PRIVADO para los comprobantes de pago (`ManualPayment.receipt`).

- Sin R2 (desarrollo, o producción local): se usa el almacenamiento por defecto
  (carpeta MEDIA_ROOT/receipts/). La ruta pública /media/ responde 404 para `receipts/`
  (ver config/media.py), así que el archivo solo sale por la vista `payment_receipt`.
- Con R2: un S3Boto3Storage propio, SIN dominio personalizado y con URLs firmadas de
  vida corta (querystring_auth). Debe definirse `RECEIPTS_BUCKET_NAME` (variable
  R2_RECEIPTS_BUCKET_NAME), los comprobantes van a ESE bucket, que debe ser privado
  (sin dominio público r2.dev ni dominio personalizado). La vista `payment_receipt`
  lee el archivo con las credenciales del servidor y lo entrega a quien tenga permiso;
  la URL firmada solo es una red de seguridad: nunca se muestra en las plantillas.
  Sin bucket separado, el acceso a comprobantes queda bloqueado; no se usa media pública.

En Cloudflare R2 "público" se activa por bucket completo, no por prefijo. Por eso la
separación real es un bucket aparte para los comprobantes.
"""
from django.conf import settings
from django.core.files.storage import Storage, default_storage

RECEIPT_URL_EXPIRE_SECONDS = 300   # una URL firmada de comprobante dura 5 minutos


class PrivateReceiptsUnavailable(OSError):
    """El bucket privado no está separado del almacenamiento público."""


class UnavailableReceiptsStorage(Storage):
    """Bloquea el acceso sin romper el arranque ni usar el bucket de media."""

    def _open(self, name, mode='rb'):
        raise PrivateReceiptsUnavailable('private_receipts_unavailable')

    def _save(self, name, content):
        raise PrivateReceiptsUnavailable('private_receipts_unavailable')

    def exists(self, name):
        return False

    def url(self, name):
        raise PrivateReceiptsUnavailable('private_receipts_unavailable')

    def size(self, name):
        raise PrivateReceiptsUnavailable('private_receipts_unavailable')


def receipts_storage():
    """Storage de los comprobantes. Django lo llama una vez al cargar el modelo."""
    if not getattr(settings, 'AWS_STORAGE_BUCKET_NAME', ''):
        return default_storage

    private_bucket = (getattr(settings, 'RECEIPTS_BUCKET_NAME', '') or '').strip()
    if not private_bucket or private_bucket == settings.AWS_STORAGE_BUCKET_NAME:
        return UnavailableReceiptsStorage()

    from storages.backends.s3boto3 import S3Boto3Storage   # solo existe/importa con R2

    return S3Boto3Storage(
        bucket_name=private_bucket,
        custom_domain=None,                       # nunca el dominio público del bucket de media
        querystring_auth=True,                    # cualquier .url() sale firmada y con vencimiento
        querystring_expire=RECEIPT_URL_EXPIRE_SECONDS,
        default_acl=None,                         # R2 no usa ACL por objeto
        file_overwrite=False,
    )
