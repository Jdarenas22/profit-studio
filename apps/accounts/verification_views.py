"""Pantallas de la verificación del correo y de la invitación (docs/contracts/verificacion-correo.md).

El registro, el login y «Agregar cliente» viven en `views.py`; aquí están las pantallas a las que llevan
los correos y el reenvío.

Cómo se protege el enlace del correo:
- El enlace (GET) solo MUESTRA una pantalla con un botón o un formulario; lo que cambia la cuenta es un
  POST con CSRF. Así un antivirus o la vista previa del buzón que abren el enlace no lo gastan ni activan
  nada solos.
- El token viaja en el cuerpo del POST, no en su URL: el rechazo de un POST (CSRF, un error) queda en el
  registro de Django con una ruta sin token. Las páginas del enlace no se guardan en caché y no envían
  `Referer` a nadie (`Referrer-Policy: origin`).
- Los enlaces que no sirven (vencidos, ya usados, manipulados) responden con una pantalla informativa
  (HTTP 200) y no con un 4xx, para que Django no escriba la ruta con el token en su registro de avisos.
"""
import logging
from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import email_verification as verification
from . import turnstile
from .axes_utils import get_client_ip
from .decorators import trainer_required
from .form_utils import errors_dict, flash_errors
from .permissions import get_client_for_trainer, reject_if_inactive
from .ratelimit import hit
from .verification_forms import InvitationPasswordForm, ResendForm

logger = logging.getLogger(__name__)

# Campos que las plantillas de esqueleto ya pintan junto al input (el resto se avisa con `messages`)
RESEND_RENDERED = ('email', 'captcha')
INVITATION_RENDERED = ('password1', 'password2')

SENSITIVE_FIELDS = ('token', 'password1', 'password2')

LOGIN_AFTER_VERIFY_MESSAGE = 'Listo, confirmamos tu correo. Inicia sesión con tu usuario y contraseña para continuar.'
LOGIN_AFTER_INVITATION_MESSAGE = 'Listo, tu contraseña quedó guardada. Inicia sesión con tu usuario y contraseña.'
RESEND_RATE_LIMIT_MESSAGE = ('Hemos recibido demasiados intentos desde tu conexión. Espera un rato e '
                             'inténtalo de nuevo, o escríbenos por WhatsApp.')
TRAINER_INVITATION_LIMIT_MESSAGE = ('Ya reenviaste esta invitación varias veces. Espera un rato antes de '
                                    'volver a enviarla.')


def private_link_page(view):
    """Páginas con un enlace firmado: sin caché y sin `Referer` hacia ningún otro sitio.

    Es `origin` y no `no-referrer` a propósito: con `no-referrer` el navegador manda `Origin: null` en el
    formulario de la página y la comprobación CSRF de Django lo rechaza (403), así que la clienta no podría
    guardar su contraseña. Con `origin` el `Origin` es el correcto y el `Referer` lleva solo el dominio, nunca
    la ruta con el token (verificado en el navegador).
    """
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response['Referrer-Policy'] = 'origin'
        return response
    return never_cache(wrapper)


def _invalid_link(request, purpose, reason):
    """Pantalla para un enlace que no sirve. HTTP 200 a propósito (ver el docstring del módulo)."""
    return render(request, 'accounts/email_link_invalid.html', {
        'purpose': purpose,
        'reason': reason,            # 'expired' | 'used' | 'invalid'
        'resend_url': reverse('verification_resend'),
        'login_url': reverse('login'),
    })


# ─── Registro: «Revisa tu correo» ─────────────────────────────────────────────

@never_cache
@require_GET
def register_check_email(request):
    """V-01. Pantalla a la que lleva el registro. Es la MISMA exista o no el correo.

    `email` es lo que la persona escribió (se guardó en su sesión al registrarse); vacío si entró directo.
    """
    return render(request, 'accounts/register_check_email.html', {
        'email': request.session.pop('register_email', ''),
        'resend_url': reverse('verification_resend'),
    })


# ─── Confirmar el correo ──────────────────────────────────────────────────────

@private_link_page
@require_GET
def email_verify(request, token):
    """V-02. El enlace del correo de confirmación: pide un clic de confirmación (el POST activa)."""
    try:
        user = verification.resolve_token(token, verification.PURPOSE_VERIFY)
    except verification.LinkError as exc:
        return _invalid_link(request, verification.PURPOSE_VERIFY, exc.reason)
    return render(request, 'accounts/email_verify_confirm.html', {
        'token': token,
        'first_name': user.first_name,
        'confirm_url': reverse('email_verify_confirm'),
    })


@private_link_page
@require_POST
@sensitive_post_parameters(*SENSITIVE_FIELDS)
def email_verify_confirm(request):
    """Activa la cuenta (`is_active=True`, `email_verified_at`) y lleva al inicio de sesión con un mensaje.

    No inicia sesión sola: quien abre el enlace entra con su usuario y contraseña, así quien registró un
    correo ajeno con una contraseña que conoce no deja a la dueña del correo dentro de su cuenta.
    """
    try:
        user = verification.verify_email(request.POST.get('token', ''))
    except verification.LinkError as exc:
        return _invalid_link(request, verification.PURPOSE_VERIFY, exc.reason)
    # Recién ahora se avisa a la superusuaria: así un robot que registra correos falsos no la inunda
    from .views import _notify_new_registration
    _notify_new_registration(user, user.interested_plan)
    messages.success(request, LOGIN_AFTER_VERIFY_MESSAGE)
    return redirect(reverse('login') + '?next=' + reverse('register_success'))


# ─── Invitación del entrenador: crear la contraseña ───────────────────────────

def _invitation_context(token, user, errors, form_data):
    return {
        'token': token,
        'first_name': user.first_name,
        'username': user.username,
        'errors': errors,
        'form': form_data,           # nunca lleva contraseñas
        'accept_url': reverse('invitation_set_password'),
    }


@private_link_page
@require_GET
def invitation_accept(request, token):
    """V-06. El enlace de la invitación: formulario para crear la contraseña."""
    try:
        user = verification.resolve_token(token, verification.PURPOSE_INVITE)
    except verification.LinkError as exc:
        return _invalid_link(request, verification.PURPOSE_INVITE, exc.reason)
    return render(request, 'accounts/invitation_set_password.html', _invitation_context(token, user, {}, {}))


@private_link_page
@require_POST
@sensitive_post_parameters(*SENSITIVE_FIELDS)
@sensitive_variables()
def invitation_set_password(request):
    """Guarda la contraseña de la clienta invitada y la marca como verificada. No inicia sesión sola."""
    token = request.POST.get('token', '')
    try:
        user = verification.resolve_token(token, verification.PURPOSE_INVITE)
    except verification.LinkError as exc:
        return _invalid_link(request, verification.PURPOSE_INVITE, exc.reason)

    form = InvitationPasswordForm(request.POST, user=user)
    if form.is_valid():
        try:
            verification.accept_invitation(token, form.cleaned_data['password1'])
        except verification.LinkError as exc:     # carrera: otro clic usó el enlace entre la lectura y el guardado
            return _invalid_link(request, verification.PURPOSE_INVITE, exc.reason)
        messages.success(request, LOGIN_AFTER_INVITATION_MESSAGE)
        return redirect('login')

    errors = errors_dict(form)
    flash_errors(request, form, errors, skip=INVITATION_RENDERED)
    return render(request, 'accounts/invitation_set_password.html', _invitation_context(token, user, errors, {}))


# ─── Reenviar el correo ───────────────────────────────────────────────────────

def _resend_context(errors=None, form_data=None, sent=False):
    return {
        'errors': errors or {},
        'form': form_data or {},
        'sent': sent,
        **turnstile.widget_context(),
    }


@never_cache
@require_http_methods(['GET', 'POST'])
def verification_resend(request):
    """V-04. «Reenviar el correo»: la respuesta es la misma haya o no una cuenta pendiente con ese correo.

    Límites: por IP (429 con `Retry-After`) y por correo (en silencio: si se pasó, no se envía y la
    respuesta no cambia). Con Turnstile encendido, también exige la comprobación.
    """
    if request.method != 'POST':
        return render(request, 'accounts/verification_resend.html', _resend_context())

    ip = get_client_ip(request) or 'desconocida'
    if not hit('verification-resend-ip', ip, verification.RESEND_IP_LIMIT, verification.RESEND_IP_WINDOW_SECONDS):
        messages.error(request, RESEND_RATE_LIMIT_MESSAGE)
        response = render(request, 'accounts/verification_resend.html', _resend_context(), status=429)
        response['Retry-After'] = str(verification.RESEND_IP_WINDOW_SECONDS)
        return response

    if not turnstile.verify(request.POST.get(turnstile.FORM_FIELD), ip):
        return render(request, 'accounts/verification_resend.html', _resend_context(
            errors={'captcha': turnstile.MESSAGE}, form_data={'email': request.POST.get('email', '')}))

    form = ResendForm(request.POST)
    if not form.is_valid():
        errors = errors_dict(form)
        flash_errors(request, form, errors, skip=RESEND_RENDERED)
        return render(request, 'accounts/verification_resend.html', _resend_context(
            errors=errors, form_data={'email': request.POST.get('email', '')}))

    email = form.cleaned_data['email']
    verification.resend_for_email(request, email)       # no dice qué encontró
    return render(request, 'accounts/verification_resend.html', _resend_context(
        form_data={'email': email}, sent=True))


# ─── El entrenador reenvía una invitación ─────────────────────────────────────

@require_POST
@trainer_required
def trainer_client_resend_invitation(request, pk):
    """Reenvía la invitación a una clienta que todavía no creó su contraseña (solo POST).

    La clienta debe ser accesible para quien pide (`get_client_for_trainer`: otro entrenador recibe 404) y
    estar activa. Límite por clienta y por quien reenvía.
    """
    client = get_client_for_trainer(request, pk)
    blocked = reject_if_inactive(request, client)
    if blocked:
        return blocked
    if not client.is_invitation_pending:
        messages.info(request, 'Esta clienta ya no tiene una invitación pendiente.')
        return redirect('trainer_client_detail', pk=client.pk)
    if not client.email:
        messages.error(request, 'La cuenta no tiene correo. Agrégalo en «Editar» para poder invitarla.')
        return redirect('trainer_client_detail', pk=client.pk)
    # Se cuentan los dos límites siempre (sin cortocircuito) para que un intento bloqueado también cuente
    client_ok = hit('invitation-resend-client', client.pk, verification.INVITE_RESEND_CLIENT_LIMIT,
                    verification.INVITE_RESEND_WINDOW_SECONDS)
    actor_ok = hit('invitation-resend-actor', request.user.pk, verification.INVITE_RESEND_ACTOR_LIMIT,
                   verification.INVITE_RESEND_WINDOW_SECONDS)
    if not (client_ok and actor_ok):
        logger.warning('invitation resend rate limited actor_id=%s client_id=%s', request.user.pk, client.pk)
        messages.error(request, TRAINER_INVITATION_LIMIT_MESSAGE)
        return redirect('trainer_client_detail', pk=client.pk)
    if verification.send_invitation_email(request, client):
        messages.success(request, f'Reenviamos la invitación a {client.email}.')
    else:
        messages.error(request, 'No pudimos enviar el correo. Inténtalo de nuevo en unos minutos.')
    return redirect('trainer_client_detail', pk=client.pk)


@require_POST
@trainer_required
def trainer_client_show_invitation_link(request, pk):
    """Prepara el enlace de invitación para mostrarlo una vez en la ficha (solo POST).

    Mismas reglas de acceso y el mismo límite por entrenadora que reenviar.
    """
    from .views import INVITATION_LINK_SESSION_KEY
    client = get_client_for_trainer(request, pk)
    blocked = reject_if_inactive(request, client)
    if blocked:
        return blocked
    if not client.is_invitation_pending:
        messages.info(request, 'Esta clienta ya no tiene una invitación pendiente.')
        return redirect('trainer_client_detail', pk=client.pk)
    if not client.email:
        messages.error(request, 'La cuenta no tiene correo. Agrégalo en «Editar» para poder invitarla.')
        return redirect('trainer_client_detail', pk=client.pk)
    client_ok = hit('invitation-resend-client', client.pk, verification.INVITE_RESEND_CLIENT_LIMIT,
                    verification.INVITE_RESEND_WINDOW_SECONDS)
    actor_ok = hit('invitation-resend-actor', request.user.pk, verification.INVITE_RESEND_ACTOR_LIMIT,
                   verification.INVITE_RESEND_WINDOW_SECONDS)
    if not (client_ok and actor_ok):
        messages.error(request, TRAINER_INVITATION_LIMIT_MESSAGE)
        return redirect('trainer_client_detail', pk=client.pk)
    request.session[INVITATION_LINK_SESSION_KEY] = {
        'pk': client.pk, 'url': verification.invitation_url(request, client)}
    return redirect('trainer_client_detail', pk=client.pk)
