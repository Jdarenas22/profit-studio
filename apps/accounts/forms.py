"""Formularios de cuentas: registro, clientes, perfil y entrenadores.

Los nombres de campo (y por tanto las claves de `errors`/`form` que leen las
plantillas) son los mismos que usaban las vistas con `request.POST[...]`.
La validación vive aquí; las vistas solo coordinan HTTP.
"""
import re

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from apps.memberships.models import MembershipPlan

from .form_utils import SafeImageField
from .models import User

# Letras (con tildes/ñ), espacios, punto, guion y apóstrofe; debe empezar por una letra.
NAME_PATTERN = re.compile(r"^(?=[^\W\d_])(?:[^\W\d_]|[ '.\-])+$")
USERNAME_PATTERN = re.compile(r'^[a-z0-9._]+$')
PHONE_ALLOWED = re.compile(r'^\+?[0-9 ()\-]+$')

USERNAME_MIN = 4
USERNAME_MAX = 30
PASSWORD_MAX = 128
BIO_MAX = 1500


def password_problems(password, **user_attrs):
    """Lista de mensajes (en español) con los validadores de AUTH_PASSWORD_VALIDATORS.

    Se pasa un usuario sin guardar para que `UserAttributeSimilarityValidator`
    rechace contraseñas parecidas al usuario, nombre o correo.
    """
    try:
        validate_password(password, user=User(**user_attrs))
    except ValidationError as exc:
        return list(exc.messages)
    return []


class _PersonForm(forms.Form):
    """Nombre, apellido, correo y teléfono (comunes a todos los formularios de personas)."""

    required_message = 'Requerido.'
    required_messages = {}                       # campo -> mensaje de "obligatorio"
    email_taken_message = 'Ya existe una cuenta con ese correo.'

    first_name = forms.CharField(label='Nombre', max_length=150)
    last_name = forms.CharField(label='Apellido', max_length=150)
    email = forms.EmailField(
        label='Correo', max_length=254,
        error_messages={'invalid': 'Ingresa un correo válido.'},
    )
    phone = forms.CharField(
        label='Teléfono', required=False, max_length=20,
        error_messages={'max_length': 'Máximo 20 caracteres.'},
    )

    def __init__(self, *args, exclude_pk=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.exclude_pk = exclude_pk
        default_required = str(forms.Field.default_error_messages['required'])
        for name, field in self.fields.items():
            if name in self.required_messages:
                field.error_messages['required'] = self.required_messages[name]
            elif str(field.error_messages.get('required')) == default_required:
                field.error_messages['required'] = self.required_message   # los campos con mensaje propio se respetan

    def _clean_name(self, value):
        if not NAME_PATTERN.match(value):
            raise ValidationError('Usa solo letras, espacios, puntos, guiones y apóstrofes.')
        return value

    def clean_first_name(self):
        return self._clean_name(self.cleaned_data['first_name'])

    def clean_last_name(self):
        return self._clean_name(self.cleaned_data['last_name'])

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        taken = User.objects.filter(email__iexact=email)
        if self.exclude_pk is not None:
            taken = taken.exclude(pk=self.exclude_pk)
        if taken.exists():
            raise ValidationError(self.email_taken_message)
        return email

    def clean_phone(self):
        phone = ' '.join(self.cleaned_data.get('phone', '').split())
        if not phone:
            return ''
        digits = sum(ch.isdigit() for ch in phone)
        if not PHONE_ALLOWED.match(phone) or not 7 <= digits <= 15:
            raise ValidationError('Teléfono no válido. Usa solo números, espacios, + y guiones (7 a 15 dígitos).')
        return phone


def _gender_field():
    return forms.ChoiceField(
        label='Género', required=False,
        choices=[('', '')] + list(User.GENDER_CHOICES),
        error_messages={'invalid_choice': 'Selecciona una opción válida.'},
    )


def _password_field(label, required_message, strip=False):
    return forms.CharField(
        label=label, strip=strip, required=True, max_length=PASSWORD_MAX,
        error_messages={'required': required_message,
                        'max_length': f'La contraseña es demasiado larga (máximo {PASSWORD_MAX} caracteres).'},
    )


class _UsernameForm(_PersonForm):
    """Añade `username` (minúsculas; letras, números, punto y guion bajo; único)."""

    username_taken_message = 'Nombre de usuario ya en uso.'
    current_username = None     # en edición: si no cambia, no se vuelve a exigir el formato

    username = forms.CharField(
        label='Usuario', max_length=USERNAME_MAX,
        error_messages={'max_length': f'Máximo {USERNAME_MAX} caracteres.'},
    )

    def clean_username(self):
        username = self.cleaned_data['username'].strip().lower()
        unchanged = self.current_username and username == self.current_username.lower()
        if not unchanged:
            if len(username) < USERNAME_MIN:
                raise ValidationError(f'Mínimo {USERNAME_MIN} caracteres.')
            if not USERNAME_PATTERN.match(username):
                raise ValidationError('Solo letras, números, puntos y guiones bajos.')
            taken = User.objects.filter(username__iexact=username)
            if self.exclude_pk is not None:
                taken = taken.exclude(pk=self.exclude_pk)
            if taken.exists():
                raise ValidationError(self.username_taken_message)
        return username

    def _user_attrs(self):
        data = self.cleaned_data
        return {k: data.get(k) or '' for k in ('username', 'email', 'first_name', 'last_name')}


# ─── Registro público ─────────────────────────────────────────────────────────

class RegisterForm(_UsernameForm):
    """Auto-registro. Mensajes iguales a los de la vista anterior, salvo el correo repetido.

    El aviso de "correo ya registrado" es GENÉRICO (no confirma que exista la cuenta):
    reduce la enumeración de correos. El nombre de usuario sí informa si está ocupado,
    porque la persona necesita elegir otro para poder registrarse.
    """

    required_messages = {
        'first_name': 'El nombre es obligatorio.',
        'last_name': 'El apellido es obligatorio.',
        'email': 'El correo es obligatorio.',
        'username': 'El usuario es obligatorio.',
    }
    email_taken_message = ('No pudimos usar ese correo. Si ya tienes una cuenta, '
                           'inicia sesión o recupera tu contraseña.')
    username_taken_message = 'Ese nombre de usuario ya está en uso.'

    gender = _gender_field()
    training_goal = forms.ChoiceField(
        label='Meta de entrenamiento', required=False,
        choices=[('', '')] + list(User.GOAL_CHOICES),
        error_messages={'invalid_choice': 'Selecciona una meta válida.'},
    )
    interested_plan = forms.CharField(label='Plan de interés', required=False, max_length=20)
    password1 = _password_field('Contraseña', 'La contraseña debe tener mínimo 8 caracteres.')
    password2 = _password_field('Repetir contraseña', 'Las contraseñas no coinciden.')

    def clean_interested_plan(self):
        # Un plan inexistente o desactivado (p. ej. enlace viejo ?plan=9) se ignora: no es un error de la persona.
        raw = self.cleaned_data.get('interested_plan', '')
        if not raw.isdigit():
            return None
        return MembershipPlan.objects.filter(pk=int(raw), is_active=True).first()

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get('password1'), cleaned.get('password2')
        if p1:
            problems = password_problems(p1, **self._user_attrs())
            if problems:
                self.add_error('password1', problems)
            if p2 != p1 and 'password2' not in self.errors:
                self.add_error('password2', 'Las contraseñas no coinciden.')
        return cleaned


# ─── Panel del entrenador: clientes ───────────────────────────────────────────

class TrainerAddClientForm(_UsernameForm):
    gender = _gender_field()
    password = _password_field('Contraseña inicial', 'Mínimo 8 caracteres.')
    activate_plan = forms.ModelChoiceField(
        label='Plan a activar', required=False, empty_label=None,
        queryset=MembershipPlan.objects.filter(is_active=True),
        error_messages={'invalid_choice': 'Plan no válido.'},
    )

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get('password')
        if password:
            problems = password_problems(password, **self._user_attrs())
            if problems:
                self.add_error('password', problems)
        return cleaned


class TrainerClientEditForm(_UsernameForm):
    gender = _gender_field()
    interested_plan = forms.ModelChoiceField(
        label='Plan de interés', required=False, empty_label=None,
        queryset=MembershipPlan.objects.filter(is_active=True),
        error_messages={'invalid_choice': 'Plan no válido.'},
    )
    new_password = forms.CharField(
        label='Nueva contraseña', required=False, strip=False, max_length=PASSWORD_MAX,
        error_messages={'max_length': f'La contraseña es demasiado larga (máximo {PASSWORD_MAX} caracteres).'},
    )

    def __init__(self, *args, client, **kwargs):
        super().__init__(*args, exclude_pk=client.pk, **kwargs)
        self.current_username = client.username

    def clean(self):
        cleaned = super().clean()
        new_password = cleaned.get('new_password')
        if new_password:
            problems = password_problems(new_password, **self._user_attrs())
            if problems:
                self.add_error('new_password', problems)
        return cleaned


# ─── Perfil propio ────────────────────────────────────────────────────────────

class MemberProfileForm(_PersonForm):
    required_messages = {
        'first_name': 'El nombre es obligatorio.',
        'last_name': 'El apellido es obligatorio.',
        'email': 'El correo es obligatorio.',
    }

    gender = _gender_field()
    bio = forms.CharField(
        label='Descripción', required=False, max_length=BIO_MAX,
        error_messages={'max_length': f'Máximo {BIO_MAX} caracteres.'},
    )
    profile_photo = SafeImageField(label='Foto de perfil', required=False)
    current_password = forms.CharField(label='Contraseña actual', required=False, strip=False,
                                       max_length=PASSWORD_MAX)
    new_password = forms.CharField(label='Nueva contraseña', required=False, strip=False,
                                   max_length=PASSWORD_MAX)
    confirm_password = forms.CharField(label='Confirmar contraseña', required=False, strip=False,
                                       max_length=PASSWORD_MAX)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, exclude_pk=user.pk, **kwargs)
        self.user = user

    def clean(self):
        cleaned = super().clean()
        current, new, confirm = (cleaned.get(k) or '' for k in
                                 ('current_password', 'new_password', 'confirm_password'))
        if new or current:
            if not self.user.check_password(current):
                self.add_error('current_password', 'Contraseña actual incorrecta.')
            elif not new:
                self.add_error('new_password', 'Escribe la nueva contraseña.')
            else:
                attrs = {
                    'username': self.user.username,
                    'email': cleaned.get('email') or self.user.email,
                    'first_name': cleaned.get('first_name') or self.user.first_name,
                    'last_name': cleaned.get('last_name') or self.user.last_name,
                }
                problems = password_problems(new, **attrs)
                if problems:
                    self.add_error('new_password', problems)
                elif new != confirm:
                    self.add_error('confirm_password', 'Las contraseñas no coinciden.')
        return cleaned


# ─── Panel de la superusuaria: entrenadores ──────────────────────────────────

class TrainerCreateTrainerForm(_UsernameForm):
    bio = forms.CharField(
        label='Descripción', required=False, max_length=BIO_MAX,
        error_messages={'max_length': f'Máximo {BIO_MAX} caracteres.'},
    )
    password = _password_field('Contraseña', 'Mínimo 8 caracteres.')
    password2 = _password_field('Repetir contraseña', 'Las contraseñas no coinciden.')

    def clean(self):
        cleaned = super().clean()
        password, password2 = cleaned.get('password'), cleaned.get('password2')
        if password:
            problems = password_problems(password, **self._user_attrs())
            if problems:
                self.add_error('password', problems)
            if password2 != password and 'password2' not in self.errors:
                self.add_error('password2', 'Las contraseñas no coinciden.')
        return cleaned


class TrainerEditForm(_PersonForm):
    bio = forms.CharField(
        label='Descripción', required=False, max_length=BIO_MAX,
        error_messages={'max_length': f'Máximo {BIO_MAX} caracteres.'},
    )
    profile_photo = SafeImageField(label='Foto de perfil', required=False)

    def __init__(self, *args, trainer, **kwargs):
        super().__init__(*args, exclude_pk=trainer.pk, **kwargs)
