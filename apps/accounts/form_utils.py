"""Utilidades compartidas por los formularios de todas las apps.

- errors_dict / flash_errors: puentes entre un `forms.Form` y las plantillas
  actuales, que leen un diccionario `errors` (clave = nombre del campo) y los
  mensajes de Django (`messages`).
- SafeImageField / SafeVideoField: validación de archivos subidos en el
  SERVIDOR (el atributo `accept=` del navegador no protege nada).
- DecimalInRangeField: decimales con coma o punto, rango y redondeo, sin
  desbordar los DecimalField de los modelos.
"""
import os
from decimal import ROUND_HALF_UP, Decimal

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError

MB = 1024 * 1024

IMAGE_MAX_BYTES = 5 * MB
IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'gif'}
IMAGE_FORMATS = {'JPEG', 'PNG', 'WEBP', 'GIF'}   # lo que Pillow detecta en el contenido
IMAGE_MAX_SIDE_PX = 10000                        # evita imágenes "bomba" de descompresión

VIDEO_MAX_BYTES = 100 * MB
VIDEO_EXTENSIONS = {'mp4', 'webm', 'mov'}
VIDEO_CONTENT_TYPES = {'video/mp4', 'video/webm', 'video/quicktime'}


# ─── Puente formulario -> plantillas ──────────────────────────────────────────

def errors_dict(form):
    """{campo: 'mensaje'} con los errores del formulario (varios mensajes se unen).

    Es el mismo formato que ya consumen las plantillas: `errors.first_name`, etc.
    Los errores generales del formulario quedan bajo la clave `__all__`.
    """
    return {name: ' '.join(str(m) for m in messages_) for name, messages_ in form.errors.items()}


def flash_errors(request, form, errors, skip=()):
    """Muestra con `messages.error` los errores que la plantilla NO pinta junto al campo.

    `skip` son las claves que la plantilla ya muestra (p. ej. 'first_name'); el resto
    se avisa arriba en pantalla con el nombre del campo, para que nunca falle en silencio.
    """
    for name, text in errors.items():
        if name in skip:
            continue
        field = form.fields.get(name)
        label = str(field.label) if field is not None and field.label else ''
        # Si el mensaje ya nombra el campo ("La edad debe..."), no se repite la etiqueta
        if label and label.lower() not in text.lower():
            text = f'{label}: {text}'
        messages.error(request, text)


# ─── Campos numéricos ─────────────────────────────────────────────────────────

class DecimalInRangeField(forms.DecimalField):
    """Decimal con coma o punto, dentro de [min_value, max_value], redondeado.

    No usa max_digits/decimal_places de Django: el rango ya garantiza que cabe en
    el DecimalField del modelo, y el redondeo evita errores por exceso de decimales.
    """

    def __init__(self, *, places=2, **kwargs):
        self.places = places
        super().__init__(**kwargs)

    def to_python(self, value):
        if isinstance(value, str):
            value = value.strip().replace(',', '.')
        return super().to_python(value)   # rechaza NaN, Infinity y basura

    def clean(self, value):
        result = super().clean(value)
        if result is None:
            return None
        return result.quantize(Decimal(1).scaleb(-self.places), rounding=ROUND_HALF_UP)


# ─── Archivos subidos ─────────────────────────────────────────────────────────

def _extension(name):
    return os.path.splitext(name or '')[1].lstrip('.').lower()


class SafeImageField(forms.ImageField):
    """Imagen real (Pillow la abre y la verifica), JPG/PNG/WEBP/GIF, máx. 5 MB."""

    default_error_messages = {
        'too_big': 'La imagen pesa demasiado (máximo %(max)s MB).',
        'bad_extension': 'Formato no permitido. Usa JPG, PNG, WEBP o GIF.',
        'bad_format': 'El archivo no es una imagen JPG, PNG, WEBP o GIF válida.',
        'too_large_dimensions': 'La imagen es demasiado grande en píxeles (máximo %(px)s por lado).',
    }

    def __init__(self, *, max_bytes=IMAGE_MAX_BYTES, **kwargs):
        self.max_bytes = max_bytes
        kwargs.setdefault('error_messages', {})
        kwargs['error_messages'].setdefault('invalid_image', 'El archivo no es una imagen válida.')
        super().__init__(**kwargs)

    def to_python(self, data):
        if data is None or data is False:
            return super().to_python(data)
        # El tamaño se revisa ANTES de que Pillow lea el archivo
        if getattr(data, 'size', 0) > self.max_bytes:
            raise ValidationError(self.error_messages['too_big'], code='too_big',
                                  params={'max': self.max_bytes // MB})
        if _extension(getattr(data, 'name', '')) not in IMAGE_EXTENSIONS:
            raise ValidationError(self.error_messages['bad_extension'], code='bad_extension')
        f = super().to_python(data)   # ImageField: abre y verifica con Pillow
        if f is None:
            return f
        image = getattr(f, 'image', None)
        if image is None or image.format not in IMAGE_FORMATS:
            raise ValidationError(self.error_messages['bad_format'], code='bad_format')
        if max(image.size) > IMAGE_MAX_SIDE_PX:
            raise ValidationError(self.error_messages['too_large_dimensions'],
                                  code='too_large_dimensions', params={'px': IMAGE_MAX_SIDE_PX})
        return f


class SafeVideoField(forms.FileField):
    """Video MP4/WEBM/MOV, máx. 100 MB; extensión, content-type y firma del archivo coinciden."""

    default_error_messages = {
        'too_big': 'El video pesa demasiado (máximo %(max)s MB). Usa un enlace de YouTube.',
        'bad_extension': 'Formato no permitido. Usa MP4, WEBM o MOV.',
        'bad_content_type': 'El archivo no parece un video MP4, WEBM o MOV.',
        'bad_signature': 'El contenido del archivo no corresponde a un video MP4, WEBM o MOV.',
    }

    def __init__(self, *, max_bytes=VIDEO_MAX_BYTES, **kwargs):
        self.max_bytes = max_bytes
        super().__init__(**kwargs)

    def to_python(self, data):
        f = super().to_python(data)
        if f is None:
            return f
        if f.size > self.max_bytes:
            raise ValidationError(self.error_messages['too_big'], code='too_big',
                                  params={'max': self.max_bytes // MB})
        ext = _extension(f.name)
        if ext not in VIDEO_EXTENSIONS:
            raise ValidationError(self.error_messages['bad_extension'], code='bad_extension')
        content_type = (getattr(f, 'content_type', '') or '').split(';')[0].strip().lower()
        if content_type not in VIDEO_CONTENT_TYPES:
            raise ValidationError(self.error_messages['bad_content_type'], code='bad_content_type')
        # El content-type lo declara el navegador (se puede falsear): se confirma con los
        # primeros bytes. MP4/MOV llevan 'ftyp' en la posición 4; WEBM empieza con la
        # cabecera EBML 1A 45 DF A3.
        f.seek(0)
        head = f.read(12)
        f.seek(0)
        if ext == 'webm':
            ok = head[:4] == b'\x1a\x45\xdf\xa3'
        else:
            ok = head[4:8] == b'ftyp'
        if not ok:
            raise ValidationError(self.error_messages['bad_signature'], code='bad_signature')
        return f
