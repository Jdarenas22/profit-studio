"""Formularios de rutinas: rutina, día y ejercicio dentro de un día.

El destinatario de la rutina (`client`) NO se valida aquí: es una decisión de
autorización y la resuelve la vista con `clients_for_trainer` (404 si no es accesible).
"""
import re

from django import forms

from apps.exercises.models import Exercise

SETS_MIN, SETS_MAX = 1, 20           # igual que el atributo min/max del formulario HTML
REST_MIN, REST_MAX = 0, 600
REPS_MAX = 50
# Repeticiones en texto libre acotado: "10", "10-12", "30 seg", "8/8", "al fallo", "AMRAP"
REPS_PATTERN = re.compile(r"^[\w\s\-–—/+×x.,:()'%]+$")


class RoutineForm(forms.Form):
    name = forms.CharField(
        label='Nombre', max_length=200,
        error_messages={'required': 'El nombre de la rutina es obligatorio.',
                        'max_length': 'Máximo 200 caracteres.'},
    )
    notes = forms.CharField(
        label='Notas', required=False, max_length=2000,
        error_messages={'max_length': 'Máximo 2000 caracteres.'},
    )


class RoutineDayForm(forms.Form):
    """El campo se llama `day_name` porque así lo envía la plantilla."""

    day_name = forms.CharField(
        label='Nombre del día', max_length=100,
        error_messages={'required': 'El nombre del día es obligatorio.',
                        'max_length': 'El nombre del día admite máximo 100 caracteres.'},
    )


class RoutineExerciseForm(forms.Form):
    exercise = forms.ModelChoiceField(
        label='Ejercicio', queryset=Exercise.objects.filter(is_active=True), empty_label=None,
        error_messages={'required': 'Selecciona un ejercicio.',
                        'invalid_choice': 'Selecciona un ejercicio válido.'},
    )
    sets = forms.IntegerField(
        label='Series', required=False, min_value=SETS_MIN, max_value=SETS_MAX,
        error_messages={'invalid': f'Las series deben ser un número entero entre {SETS_MIN} y {SETS_MAX}.',
                        'min_value': f'Las series deben estar entre {SETS_MIN} y {SETS_MAX}.',
                        'max_value': f'Las series deben estar entre {SETS_MIN} y {SETS_MAX}.'},
    )
    reps = forms.CharField(
        label='Repeticiones', required=False, max_length=REPS_MAX,
        error_messages={'max_length': f'Las repeticiones admiten máximo {REPS_MAX} caracteres.'},
    )
    rest_seconds = forms.IntegerField(
        label='Descanso', required=False, min_value=REST_MIN, max_value=REST_MAX,
        error_messages={'invalid': f'El descanso debe ser un número entero de segundos entre {REST_MIN} y {REST_MAX}.',
                        'min_value': f'El descanso debe estar entre {REST_MIN} y {REST_MAX} segundos.',
                        'max_value': f'El descanso debe estar entre {REST_MIN} y {REST_MAX} segundos.'},
    )
    observations = forms.CharField(
        label='Observaciones', required=False, max_length=500,
        error_messages={'max_length': 'Las observaciones admiten máximo 500 caracteres.'},
    )

    # Valores por defecto de la interfaz cuando el campo no se envía
    DEFAULT_SETS = 3
    DEFAULT_REPS = '10'
    DEFAULT_REST = 60

    def clean_sets(self):
        value = self.cleaned_data.get('sets')
        return self.DEFAULT_SETS if value is None else value

    def clean_rest_seconds(self):
        value = self.cleaned_data.get('rest_seconds')
        return self.DEFAULT_REST if value is None else value

    def clean_reps(self):
        value = self.cleaned_data.get('reps') or self.DEFAULT_REPS
        if not REPS_PATTERN.match(value):
            raise forms.ValidationError('Las repeticiones solo admiten letras, números y signos simples (por ejemplo 10-12).')
        return value
