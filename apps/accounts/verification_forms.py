"""Formularios de la verificación del correo y de la invitación (docs/contracts/verificacion-correo.md).

Mismo puente que el resto de `accounts`: las vistas pasan `errors_dict(form)` a las plantillas.
"""
from django import forms

from .forms import PASSWORD_MAX, password_problems

PASSWORD_REQUIRED_MESSAGE = 'La contraseña debe tener mínimo 8 caracteres.'
PASSWORD_MISMATCH_MESSAGE = 'Las contraseñas no coinciden.'


def _password_field(label, required_message):
    return forms.CharField(
        label=label, strip=False, required=True, max_length=PASSWORD_MAX,
        error_messages={'required': required_message,
                        'max_length': f'La contraseña es demasiado larga (máximo {PASSWORD_MAX} caracteres).'},
    )


class ResendForm(forms.Form):
    """«Reenviar el correo»: solo el correo. Un correo mal escrito es un error del campo (no revela nada
    de ninguna cuenta); lo demás lo decide la vista sin cambiar la respuesta."""

    email = forms.EmailField(
        label='Correo', max_length=254,
        error_messages={'required': 'Escribe tu correo.', 'invalid': 'Ingresa un correo válido.'},
    )

    def clean_email(self):
        return self.cleaned_data['email'].strip().lower()


class InvitationPasswordForm(forms.Form):
    """La clienta crea su contraseña con el enlace de la invitación (`new-password`, `validate_password`).

    `user` es la cuenta del enlace: la política de contraseñas rechaza claves parecidas a su usuario,
    nombre o correo, igual que en el registro.
    """

    password1 = _password_field('Contraseña', PASSWORD_REQUIRED_MESSAGE)
    password2 = _password_field('Repetir contraseña', PASSWORD_MISMATCH_MESSAGE)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get('password1'), cleaned.get('password2')
        if p1:
            problems = password_problems(
                p1, username=self.user.username, email=self.user.email,
                first_name=self.user.first_name, last_name=self.user.last_name,
            )
            if problems:
                self.add_error('password1', problems)
            if p2 != p1 and 'password2' not in self.errors:
                self.add_error('password2', PASSWORD_MISMATCH_MESSAGE)
        return cleaned
