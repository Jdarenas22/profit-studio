"""Formularios de los planes con IA (validación en el servidor; el navegador solo ayuda)."""
from django import forms

from apps.plans.rules.validate import clean_text

from .models import KIND_CHOICES, KIND_EXERCISE
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
    kind = forms.ChoiceField(choices=KIND_CHOICES, required=False, error_messages={
        'invalid_choice': 'Elige un tipo de plan válido.'})
    request_token = forms.UUIDField(error_messages=TOKEN_ERRORS)

    def clean_kind(self):
        return self.cleaned_data.get('kind') or KIND_EXERCISE


class MealChoiceForm(forms.Form):
    slot = forms.IntegerField(min_value=1, error_messages={'required': 'Falta la comida.', 'invalid': 'Comida no válida.'})
    option = forms.IntegerField(min_value=1, error_messages={'required': 'Falta la opción.', 'invalid': 'Opción no válida.'})
    date = forms.DateField(input_formats=['%Y-%m-%d'], error_messages={
        'required': 'Falta el día.', 'invalid': 'Día no válido.'})


class CompleteForm(forms.Form):
    """"Cumplí esta comida": `1` marca, `0` desmarca. Cualquier otro valor (o ninguno) es un error."""
    done = forms.ChoiceField(choices=[('1', 'Marcar como cumplida'), ('0', 'Quitar la marca')], error_messages={
        'required': 'Falta indicar si cumpliste la comida.', 'invalid_choice': 'Valor no válido.'})


class AlternativeForm(forms.Form):
    option = forms.IntegerField(min_value=1, error_messages={'required': 'Elige la opción que quieres cambiar.',
                                                              'invalid': 'Opción no válida.'})
    request_token = forms.UUIDField(error_messages=TOKEN_ERRORS)


class ReviewOptionForm(forms.Form):
    action = forms.ChoiceField(choices=[('keep', 'Dejarla'), ('revert', 'Recuperar la anterior')],
                               error_messages={'required': 'Elige qué hacer.', 'invalid_choice': 'Acción no válida.'})


def parse_option_post(post):
    """Lee el formulario de edición de una opción: nombre, preparación y filas alimento + gramos.
    Devuelve `(name, preparation, items, errors)`; `errors` = {campo: mensaje}."""
    errors = {}
    items = []
    food_ids, grams = post.getlist('food_id'), post.getlist('grams')
    for index in range(max(len(food_ids), len(grams))):
        raw_food = (food_ids[index] if index < len(food_ids) else '').strip()
        raw_grams = (grams[index] if index < len(grams) else '').strip().replace(',', '.')
        if not raw_food and not raw_grams:
            continue          # fila vacía
        try:
            amount = float(raw_grams)
            if not raw_food or not amount.is_integer():
                raise ValueError
            items.append({'food_id': int(raw_food), 'grams': int(amount)})
        except ValueError:
            errors['items'] = 'Cada fila necesita un alimento y los gramos como número entero.'
            break
    name = clean_text(post.get('name') or '')
    preparation = clean_text(post.get('preparation') or '')
    if len(name) > 100:
        errors['name'] = 'El nombre admite máximo 100 caracteres.'
    if len(preparation) > 400:
        errors['preparation'] = 'La preparación admite máximo 400 caracteres.'
    return name, preparation, items, errors


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
