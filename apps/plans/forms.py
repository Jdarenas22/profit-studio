"""Formularios de los planes con IA (validación en el servidor; el navegador solo ayuda)."""
from django import forms

from apps.plans.rules.validate import clean_text

from .models import KIND_CHOICES
from .services import ARCHIVE_REASON_MAX, NOTE_FOR_CLIENT_MAX, TRAINER_NOTES_MAX

TOKEN_ERRORS = {'required': 'El formulario expiró. Recarga la página.',
                'invalid': 'El formulario expiró. Recarga la página.'}


class _CleanTextMixin:
    """Textos libres: normalizados, sin caracteres invisibles y sin `<` ni `>` (siempre se
    muestran escapados; la nota del entrenador además viaja a la IA)."""

    def _clean_free_text(self, name):
        value = clean_text(self.cleaned_data.get(name) or '')
        if '<' in value or '>' in value:
            raise forms.ValidationError('No uses los signos < ni >.')
        return value


class PlanRequestForm(forms.Form):
    kind = forms.ChoiceField(choices=KIND_CHOICES, error_messages={
        'required': 'Elige el tipo de plan.', 'invalid_choice': 'Elige un tipo de plan válido.'})
    request_token = forms.UUIDField(error_messages=TOKEN_ERRORS)


class GenerateForm(forms.Form):
    request_token = forms.UUIDField(error_messages=TOKEN_ERRORS)


class ApproveForm(_CleanTextMixin, forms.Form):
    note_for_client = forms.CharField(required=False, max_length=NOTE_FOR_CLIENT_MAX, error_messages={
        'max_length': f'La nota para la clienta admite máximo {NOTE_FOR_CLIENT_MAX} caracteres.'})
    warnings_ack = forms.BooleanField(required=False)
    stale_ack = forms.BooleanField(required=False)

    def clean_note_for_client(self):
        return self._clean_free_text('note_for_client')


class RegenerateForm(_CleanTextMixin, forms.Form):
    notes = forms.CharField(required=False, max_length=TRAINER_NOTES_MAX, error_messages={
        'max_length': f'Las notas admiten máximo {TRAINER_NOTES_MAX} caracteres.'})
    request_token = forms.UUIDField(error_messages=TOKEN_ERRORS)

    def clean_notes(self):
        return self._clean_free_text('notes')


class ArchiveForm(_CleanTextMixin, forms.Form):
    reason = forms.CharField(required=False, max_length=ARCHIVE_REASON_MAX, error_messages={
        'max_length': f'El motivo admite máximo {ARCHIVE_REASON_MAX} caracteres.'})

    def clean_reason(self):
        return self._clean_free_text('reason')
