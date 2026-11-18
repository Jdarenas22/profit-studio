"""Formulario de activación de los avisos (W-01). El número se valida en `phone.normalize_phone`."""
from django import forms


class WhatsAppEnableForm(forms.Form):
    phone = forms.CharField(max_length=30, error_messages={
        'required': 'Escribe tu celular.', 'max_length': 'Máximo 30 caracteres.'})
    accept = forms.BooleanField(error_messages={
        'required': 'Debes marcar la casilla para activar los avisos.'})
    text_version = forms.IntegerField(min_value=1, error_messages={
        'required': 'Recarga la página e inténtalo de nuevo.',
        'invalid': 'Recarga la página e inténtalo de nuevo.',
        'min_value': 'Recarga la página e inténtalo de nuevo.'})
