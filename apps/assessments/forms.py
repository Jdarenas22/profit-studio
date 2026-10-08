"""Formularios de valoraciones: valoración inicial, test Ruffier-Dickson y mediciones.

Decisión sobre la ESTATURA: el modelo guarda metros (DecimalField 4,2) y la interfaz
pide metros (1,68). Si llega un valor mayor a 3 se interpreta como CENTÍMETROS y se
convierte (168 -> 1,68). Los dos rangos válidos no se solapan (0,5-2,5 m y 50-250 cm),
así que nunca hay ambigüedad; cualquier otro valor se rechaza con un mensaje claro.
"""
from decimal import Decimal

from django import forms

from apps.accounts.form_utils import DecimalInRangeField

from .models import InitialAssessment

AGE_MIN, AGE_MAX = 5, 100
WEIGHT_MIN, WEIGHT_MAX = Decimal('20'), Decimal('400')       # kg
HEIGHT_MIN, HEIGHT_MAX = Decimal('0.5'), Decimal('2.5')      # metros
CM_THRESHOLD = Decimal('3')                                   # por encima de esto se lee como cm
WAIST_MIN, WAIST_MAX = Decimal('30'), Decimal('300')         # cm
PULSE_MIN, PULSE_MAX = 20, 250                                # pulsaciones por minuto
# El IMC se guarda en DecimalField(5,2) (máx. 999,99). Fuera de 8-100 el par peso/estatura
# no es creíble (error de tecleo) y, en el extremo, desbordaría la columna en PostgreSQL.
IMC_MIN, IMC_MAX = 8.0, 100.0

HEIGHT_MESSAGE = 'La estatura debe estar entre 0,5 y 2,5 metros (por ejemplo 1,68) o entre 50 y 250 cm.'
WEIGHT_MESSAGE = f'El peso debe estar entre {WEIGHT_MIN} y {WEIGHT_MAX} kg.'
IMC_MESSAGE = 'El peso y la estatura no son coherentes entre sí (IMC fuera de 8 a 100). Revisa ambos valores.'


class HeightField(DecimalInRangeField):
    """Estatura en metros; acepta centímetros (valor > 3) y los convierte."""

    def __init__(self, **kwargs):
        kwargs.setdefault('min_value', HEIGHT_MIN)
        kwargs.setdefault('max_value', HEIGHT_MAX)
        kwargs.setdefault('error_messages', {})
        for key in ('invalid', 'min_value', 'max_value'):
            kwargs['error_messages'].setdefault(key, HEIGHT_MESSAGE)
        super().__init__(places=2, **kwargs)

    def to_python(self, value):
        value = super().to_python(value)
        if value is not None and value.is_finite() and value > CM_THRESHOLD:
            value = value / 100
        return value


def _weight_field(places, required=True):
    return DecimalInRangeField(
        label='Peso', required=required, places=places,
        min_value=WEIGHT_MIN, max_value=WEIGHT_MAX,
        error_messages={'required': 'El peso es obligatorio.', 'invalid': 'Peso inválido.',
                        'min_value': WEIGHT_MESSAGE, 'max_value': WEIGHT_MESSAGE},
    )


def _check_imc(form, weight, height):
    if weight is not None and height is not None:
        imc = float(weight) / (float(height) ** 2)
        if not IMC_MIN <= imc <= IMC_MAX:
            form.add_error('height', IMC_MESSAGE)


def _text_field(label, max_length, required=False, **kwargs):
    return forms.CharField(
        label=label, required=required, max_length=max_length,
        error_messages={'required': 'Este campo es obligatorio.',
                        'max_length': f'Máximo {max_length} caracteres.'},
        **kwargs,
    )


class InitialAssessmentForm(forms.Form):
    age = forms.IntegerField(
        label='Edad', min_value=AGE_MIN, max_value=AGE_MAX,
        error_messages={'required': 'La edad es obligatoria.',
                        'invalid': f'La edad debe ser un número entero entre {AGE_MIN} y {AGE_MAX}.',
                        'min_value': f'La edad debe estar entre {AGE_MIN} y {AGE_MAX} años.',
                        'max_value': f'La edad debe estar entre {AGE_MIN} y {AGE_MAX} años.'},
    )
    sex = forms.ChoiceField(
        label='Sexo', choices=InitialAssessment.SEX_CHOICES,
        error_messages={'required': 'Selecciona el sexo.', 'invalid_choice': 'Selecciona una opción válida.'},
    )
    weight = _weight_field(places=2)
    height = HeightField(label='Estatura', error_messages={'required': 'La estatura es obligatoria.'})
    goal = _text_field('Objetivo', 1000, required=True)
    physical_restrictions = _text_field('Restricciones físicas', 2000)
    observations = _text_field('Observaciones', 2000)

    # Test de Ruffier-Dickson (opcional: los tres pulsos o ninguno)
    p0 = forms.IntegerField(label='P0', required=False, min_value=PULSE_MIN, max_value=PULSE_MAX)
    p1 = forms.IntegerField(label='P1', required=False, min_value=PULSE_MIN, max_value=PULSE_MAX)
    p2 = forms.IntegerField(label='P2', required=False, min_value=PULSE_MIN, max_value=PULSE_MAX)
    dixon_observations = _text_field('Observaciones del test', 1000)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        pulse_message = f'El pulso debe ser un número entero entre {PULSE_MIN} y {PULSE_MAX}.'
        for name in ('p0', 'p1', 'p2'):
            for key in ('invalid', 'min_value', 'max_value'):
                self.fields[name].error_messages[key] = pulse_message

    @property
    def has_dixon(self):
        return all(self.cleaned_data.get(k) is not None for k in ('p0', 'p1', 'p2'))

    def clean(self):
        cleaned = super().clean()
        _check_imc(self, cleaned.get('weight'), cleaned.get('height'))
        pulses = [cleaned.get(k) for k in ('p0', 'p1', 'p2')]
        filled = [p for p in pulses if p is not None]
        no_errors_in_pulses = not any(k in self.errors for k in ('p0', 'p1', 'p2'))
        if no_errors_in_pulses and filled and len(filled) < 3:
            self.add_error('p0', 'Completa los tres pulsos (P0, P1 y P2) o deja los tres en blanco.')
        return cleaned


class DixonTestForm(forms.Form):
    p0 = forms.IntegerField(label='P0', min_value=PULSE_MIN, max_value=PULSE_MAX)
    p1 = forms.IntegerField(label='P1', min_value=PULSE_MIN, max_value=PULSE_MAX)
    p2 = forms.IntegerField(label='P2', min_value=PULSE_MIN, max_value=PULSE_MAX)
    dixon_observations = _text_field('Observaciones del test', 1000)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        pulse_message = f'El pulso debe ser un número entero entre {PULSE_MIN} y {PULSE_MAX}.'
        for name in ('p0', 'p1', 'p2'):
            for key in ('required', 'invalid', 'min_value', 'max_value'):
                self.fields[name].error_messages[key] = pulse_message


class BodyMeasurementForm(forms.Form):
    """Medición corporal. Claves iguales a las de la plantilla: weight, height, waist_cm, notes."""

    weight = _weight_field(places=1)
    height = HeightField(label='Estatura', required=False)
    waist_cm = DecimalInRangeField(
        label='Cintura (cm)', required=False, places=1,
        min_value=WAIST_MIN, max_value=WAIST_MAX,
        error_messages={'invalid': 'Cintura inválida.',
                        'min_value': f'La cintura debe estar entre {WAIST_MIN} y {WAIST_MAX} cm.',
                        'max_value': f'La cintura debe estar entre {WAIST_MIN} y {WAIST_MAX} cm.'},
    )
    notes = _text_field('Observaciones', 2000)

    def clean(self):
        cleaned = super().clean()
        _check_imc(self, cleaned.get('weight'), cleaned.get('height'))
        return cleaned
