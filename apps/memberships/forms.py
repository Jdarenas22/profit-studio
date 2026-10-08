"""Formulario de gestión de membresía (activar, renovar, desactivar)."""
from django import forms

from .models import MembershipPlan

DURATION_MIN, DURATION_MAX = 1, 3650     # días (hasta 10 años)
NOTES_MAX = 1000

ACTIVATE, RENEW, DEACTIVATE = 'activate', 'renew', 'deactivate'
ACTION_CHOICES = [(ACTIVATE, 'Activar'), (RENEW, 'Renovar'), (DEACTIVATE, 'Desactivar')]


class MembershipManageForm(forms.Form):
    action = forms.ChoiceField(
        label='Acción', choices=ACTION_CHOICES,
        error_messages={'required': 'Acción no válida.', 'invalid_choice': 'Acción no válida.'},
    )
    plan_id = forms.ModelChoiceField(
        label='Plan', required=False, empty_label=None,
        queryset=MembershipPlan.objects.filter(is_active=True),
        error_messages={'invalid_choice': 'Selecciona un plan válido.'},
    )
    duration_days = forms.IntegerField(
        label='Duración (días)', required=False, min_value=DURATION_MIN, max_value=DURATION_MAX,
        error_messages={
            'invalid': f'La duración debe ser un número entero de días entre {DURATION_MIN} y {DURATION_MAX}.',
            'min_value': f'La duración debe estar entre {DURATION_MIN} y {DURATION_MAX} días.',
            'max_value': f'La duración debe estar entre {DURATION_MIN} y {DURATION_MAX} días.',
        },
    )
    notes = forms.CharField(
        label='Notas', required=False, max_length=NOTES_MAX,
        error_messages={'max_length': f'Las notas admiten máximo {NOTES_MAX} caracteres.'},
    )

    def __init__(self, *args, membership=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.membership = membership

    def clean(self):
        cleaned = super().clean()
        action = cleaned.get('action')
        if action not in (ACTIVATE, RENEW) or self.errors:
            return cleaned
        plan = cleaned.get('plan_id')
        if action == ACTIVATE and plan is None:
            self.add_error('plan_id', 'Selecciona un plan para activar la membresía.')
            return cleaned
        # Duración: la indicada, o la del plan elegido, o (al renovar) la del plan actual
        if cleaned.get('duration_days') is None:
            fallback = plan or getattr(self.membership, 'plan', None)
            if fallback is None:
                self.add_error('duration_days', 'Indica la duración en días o elige un plan.')
            else:
                cleaned['duration_days'] = fallback.duration_days
        return cleaned
