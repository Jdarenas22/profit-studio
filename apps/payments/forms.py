"""Formularios de pagos: validación de entradas del pago manual (monto, fecha, método, comprobante)."""
import os
import re
import warnings

from django import forms
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

from apps.memberships.models import MembershipPlan
from .models import ManualPayment

MAX_AMOUNT = 100_000_000          # COP; tope de cordura (el campo admite hasta ~2.147 millones)
MAX_RECEIPT_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_IMAGE_PIXELS = 40_000_000     # evita "bombas" de descompresión

# extensión permitida -> formato real que debe tener el contenido
ALLOWED_RECEIPTS = {
    '.jpg': 'JPEG',
    '.jpeg': 'JPEG',
    '.png': 'PNG',
    '.webp': 'WEBP',
    '.pdf': 'PDF',
}

_AMOUNT_PLAIN = re.compile(r'^[0-9]+$')                       # solo dígitos ASCII
_AMOUNT_GROUPED = re.compile(r'^[0-9]{1,3}([.,][0-9]{3})+$')   # 150.000 / 1,500,000


def validate_receipt_file(upload):
    """Valida tamaño, extensión y CONTENIDO real del comprobante (no confía en Content-Type)."""
    if upload.size > MAX_RECEIPT_BYTES:
        raise forms.ValidationError('El comprobante pesa más de 5 MB. Sube un archivo más liviano.')

    ext = os.path.splitext(upload.name or '')[1].lower()
    expected = ALLOWED_RECEIPTS.get(ext)
    if expected is None:
        raise forms.ValidationError('Tipo de archivo no permitido. Usa JPG, PNG, WEBP o PDF.')

    upload.seek(0)
    try:
        if expected == 'PDF':
            if upload.read(5) != b'%PDF-':
                raise forms.ValidationError('El archivo no es un PDF válido.')
        else:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter('error', Image.DecompressionBombWarning)
                    image = Image.open(upload)
                    if image.format != expected:
                        raise forms.ValidationError('El contenido del archivo no corresponde a su extensión.')
                    width, height = image.size
                    if width * height > MAX_IMAGE_PIXELS:
                        raise forms.ValidationError('La imagen es demasiado grande en dimensiones.')
                    image.verify()
            except forms.ValidationError:
                raise
            except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning,
                    OSError, SyntaxError, ValueError):
                raise forms.ValidationError('El archivo no es una imagen válida.')
    finally:
        upload.seek(0)
    return upload


class ManualPaymentForm(forms.Form):
    amount = forms.CharField(max_length=20, error_messages={'required': 'El monto es obligatorio.'})
    payment_date = forms.DateField(
        error_messages={'required': 'La fecha es obligatoria.', 'invalid': 'Fecha inválida.'},
    )
    method = forms.ChoiceField(
        choices=ManualPayment.METHOD_CHOICES,
        error_messages={
            'required': 'Elige un método de pago.',
            'invalid_choice': 'Método de pago inválido.',
        },
    )
    plan = forms.ModelChoiceField(
        queryset=MembershipPlan.objects.none(), required=False,
        error_messages={'invalid_choice': 'Plan inválido.'},
    )
    receipt = forms.FileField(required=False)
    notes = forms.CharField(max_length=1000, required=False, strip=True,
                            error_messages={'max_length': 'Las notas no pueden superar 1000 caracteres.'})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['plan'].queryset = MembershipPlan.objects.filter(is_active=True)

    def clean_amount(self):
        raw = self.cleaned_data['amount'].strip().lstrip('$').strip()
        if _AMOUNT_PLAIN.match(raw):
            value = int(raw)
        elif _AMOUNT_GROUPED.match(raw):
            value = int(re.sub(r'[.,]', '', raw))
        else:
            raise forms.ValidationError('Monto inválido. Escribe solo números, por ejemplo 150000.')
        if value <= 0:
            raise forms.ValidationError('El monto debe ser mayor que cero.')
        if value > MAX_AMOUNT:
            raise forms.ValidationError('El monto es demasiado alto. Revisa la cifra.')
        return value

    def clean_payment_date(self):
        value = self.cleaned_data['payment_date']
        if value > timezone.localdate():
            raise forms.ValidationError('La fecha de pago no puede ser futura.')
        if value.year < 2020:
            raise forms.ValidationError('La fecha de pago es demasiado antigua.')
        return value

    def clean_receipt(self):
        upload = self.cleaned_data.get('receipt')
        if not upload:
            return None
        return validate_receipt_file(upload)

    def error_messages_by_field(self):
        """{'campo': 'mensaje'} (un solo texto por campo) — formato que usa la plantilla."""
        return {name: ' '.join(errs) for name, errs in self.errors.items()}
