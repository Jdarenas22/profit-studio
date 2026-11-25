"""Verificación del correo y creación de contraseña por invitación (docs/contracts/verificacion-correo.md).

Dos enlaces firmados, los dos con caducidad y de un solo uso, sin guardar nada en la base:

- `verify`: la persona se registró sola. La cuenta nace INACTIVA (`is_active=False`, `email_verified_at`
  vacío) y se activa al confirmar el enlace (vale 24 horas).
- `invite`: un entrenador creó la cuenta. Nace activa pero con contraseña inutilizable; la clienta crea su
  contraseña con el enlace de la invitación (vale 72 horas) y queda verificada.

El enlace es `django.core.signing` (HMAC con la `SECRET_KEY`, con fecha y `max_age`) más una HUELLA del
estado de la cuenta (id, correo, `is_active`, `email_verified_at` y hash de la contraseña): al usar el
enlace el estado cambia y la huella ya no coincide, así que el mismo enlace no sirve dos veces. Cambiar el
correo de la cuenta también lo invalida (el enlace quedó en otro buzón). Cada propósito firma con su
propia sal: un enlace de confirmar no sirve como invitación ni al revés.

Nunca se escribe un enlace ni su token en un log: los logs llevan solo el id de la cuenta y el tipo de
correo. Los correos son texto plano y no llevan datos de salud.
"""
import logging

from django.conf import settings
from django.core import signing
from django.core.mail import BadHeaderError, send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from .models import User
from .ratelimit import hit

logger = logging.getLogger(__name__)

PURPOSE_VERIFY = 'verify'
PURPOSE_INVITE = 'invite'
PURPOSES = (PURPOSE_VERIFY, PURPOSE_INVITE)

# Caducidad de cada enlace (segundos). Se pueden ajustar en settings.
VERIFY_MAX_AGE_SECONDS = getattr(settings, 'EMAIL_VERIFICATION_MAX_AGE_SECONDS', 24 * 3600)
INVITE_MAX_AGE_SECONDS = getattr(settings, 'INVITATION_MAX_AGE_SECONDS', 72 * 3600)

# Límite de frecuencia (ventana fija, `ratelimit.hit`). Se pueden ajustar en settings.
# - por correo: cuántos correos de confirmación/invitación/aviso puede recibir UNA dirección por ventana
#   (frena a quien use el registro o «Reenviar» para llenarle el buzón a otra persona);
# - por IP: cuántos envíos del formulario «Reenviar el correo» por ventana;
# - invitaciones del entrenador: por clienta y por quien las reenvía.
EMAIL_SEND_LIMIT = getattr(settings, 'EMAIL_VERIFICATION_EMAIL_LIMIT', 3)
EMAIL_SEND_WINDOW_SECONDS = getattr(settings, 'EMAIL_VERIFICATION_EMAIL_WINDOW_SECONDS', 3600)
RESEND_IP_LIMIT = getattr(settings, 'EMAIL_VERIFICATION_RESEND_IP_LIMIT', 10)
RESEND_IP_WINDOW_SECONDS = getattr(settings, 'EMAIL_VERIFICATION_RESEND_IP_WINDOW_SECONDS', 3600)
INVITE_RESEND_CLIENT_LIMIT = getattr(settings, 'INVITATION_RESEND_CLIENT_LIMIT', 3)
INVITE_RESEND_ACTOR_LIMIT = getattr(settings, 'INVITATION_RESEND_ACTOR_LIMIT', 30)
INVITE_RESEND_WINDOW_SECONDS = getattr(settings, 'INVITATION_RESEND_WINDOW_SECONDS', 3600)

MAX_TOKEN_LENGTH = 512      # un enlace real mide ~120; más que esto ni se intenta leer

# Motivos por los que un enlace no sirve (`LinkError.reason`)
REASON_INVALID = 'invalid'      # manipulado, cortado o de otro tipo
REASON_EXPIRED = 'expired'      # firma buena pero vencido
REASON_USED = 'used'            # ya se usó, o la cuenta cambió (correo, estado) desde que se envió

_SALT = 'profit.accounts.email-link.'
_FINGERPRINT_SALT = 'profit.accounts.email-link.fingerprint'

SUBJECT_TEMPLATES = {
    PURPOSE_VERIFY: 'accounts/email_verify_subject.txt',
    PURPOSE_INVITE: 'accounts/email_invite_subject.txt',
    'existing': 'accounts/email_existing_subject.txt',
}
BODY_TEMPLATES = {
    PURPOSE_VERIFY: 'accounts/email_verify.txt',
    PURPOSE_INVITE: 'accounts/email_invite.txt',
    'existing': 'accounts/email_existing.txt',
}


class LinkError(Exception):
    """El enlace no sirve. `reason` es uno de `REASON_*`; nunca lleva el token."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def max_age(purpose):
    return VERIFY_MAX_AGE_SECONDS if purpose == PURPOSE_VERIFY else INVITE_MAX_AGE_SECONDS


def valid_hours(purpose):
    return max(1, int(max_age(purpose) // 3600))


# ─── Enlaces firmados ─────────────────────────────────────────────────────────

def _fingerprint(user):
    verified = user.email_verified_at.isoformat() if user.email_verified_at else ''
    raw = f'{user.pk}|{user.email}|{int(bool(user.is_active))}|{verified}|{user.password}'
    return salted_hmac(_FINGERPRINT_SALT, raw, algorithm='sha256').hexdigest()[:32]


def make_token(user, purpose):
    """Enlace firmado para `user`. Va en la URL del correo; nunca se guarda ni se registra."""
    if purpose not in PURPOSES:
        raise ValueError('purpose')
    return signing.dumps({'u': user.pk, 'f': _fingerprint(user)}, salt=_SALT + purpose)


def _state_matches(user, purpose):
    if purpose == PURPOSE_VERIFY:
        return user.is_pending_registration
    return user.is_invitation_pending


def resolve_token(token, purpose, lock=False):
    """La cuenta a la que pertenece el enlace, o `LinkError`. No cambia nada.

    Con `lock=True` (dentro de una transacción) bloquea la fila para que dos envíos a la vez no usen el
    mismo enlace dos veces.
    """
    if purpose not in PURPOSES or not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
        raise LinkError(REASON_INVALID)
    try:
        payload = signing.loads(token, salt=_SALT + purpose, max_age=max_age(purpose))
    except signing.SignatureExpired:
        raise LinkError(REASON_EXPIRED) from None
    except signing.BadSignature:
        raise LinkError(REASON_INVALID) from None
    if not (isinstance(payload, dict) and type(payload.get('u')) is int and isinstance(payload.get('f'), str)):
        raise LinkError(REASON_INVALID)
    users = User.objects.select_for_update() if lock else User.objects
    user = users.filter(pk=payload['u']).first()
    if user is None or user.erased_at is not None:
        raise LinkError(REASON_INVALID)
    if not _state_matches(user, purpose) or not constant_time_compare(payload['f'], _fingerprint(user)):
        raise LinkError(REASON_USED)
    return user


def verify_email(token):
    """Confirma el correo de una cuenta recién registrada: la activa y la marca verificada.

    Devuelve la cuenta. `LinkError` si el enlace no sirve. Una sola transacción y fila bloqueada: dos clics
    a la vez activan una sola vez.
    """
    with transaction.atomic():
        user = resolve_token(token, PURPOSE_VERIFY, lock=True)
        user.is_active = True
        user.email_verified_at = timezone.now()
        user.save(update_fields=['is_active', 'email_verified_at'])
    logger.info('email verified user_id=%s', user.pk)
    return user


def accept_invitation(token, password):
    """La clienta crea su contraseña con el enlace de la invitación: queda con contraseña y verificada.

    La política de contraseñas la valida quien llama (`verification_forms.InvitationPasswordForm`); aquí
    solo se aplica. `LinkError` si el enlace no sirve.
    """
    with transaction.atomic():
        user = resolve_token(token, PURPOSE_INVITE, lock=True)
        user.set_password(password)
        user.email_verified_at = timezone.now()
        user.save(update_fields=['password', 'email_verified_at'])
    logger.info('invitation accepted user_id=%s', user.pk)
    return user


# ─── Correos ──────────────────────────────────────────────────────────────────

def _absolute_url(request, name, token):
    return request.build_absolute_uri(reverse(name, args=[token]))


def _send(request, user, kind, url=None, purpose=None):
    """Arma y envía un correo de texto plano. `True` si salió; un fallo de correo nunca rompe la petición."""
    context = {
        'user': user, 'url': url, 'valid_hours': valid_hours(purpose) if purpose else None,
        'login_url': request.build_absolute_uri(reverse('login')),
        'reset_url': request.build_absolute_uri(reverse('password_reset')),
        'site_name': 'ProFit Studio',
    }
    subject = ''.join(render_to_string(SUBJECT_TEMPLATES[kind], context).splitlines()).strip()
    body = render_to_string(BODY_TEMPLATES[kind], context)
    try:
        send_mail(subject, body, getattr(settings, 'DEFAULT_FROM_EMAIL', None), [user.email],
                  fail_silently=False)
    except (BadHeaderError, OSError) as exc:     # OSError incluye smtplib.SMTPException y los fallos de red
        logger.warning('account email failed kind=%s user_id=%s error=%s', kind, user.pk, type(exc).__name__)
        return False
    logger.info('account email sent kind=%s user_id=%s', kind, user.pk)
    return True


def send_verification_email(request, user):
    token = make_token(user, PURPOSE_VERIFY)
    return _send(request, user, PURPOSE_VERIFY, _absolute_url(request, 'email_verify', token), PURPOSE_VERIFY)


def invitation_url(request, user):
    """Enlace de invitación de `user` (para que la entrenadora lo entregue en persona). Es un secreto."""
    return _absolute_url(request, 'invitation_accept', make_token(user, PURPOSE_INVITE))


def send_invitation_email(request, user):
    token = make_token(user, PURPOSE_INVITE)
    return _send(request, user, PURPOSE_INVITE, _absolute_url(request, 'invitation_accept', token),
                 PURPOSE_INVITE)


def send_existing_account_notice(request, user):
    """Aviso a la dueña de un correo que ya tiene cuenta y alguien volvió a registrarlo. Sin enlace firmado."""
    return _send(request, user, 'existing')


def send_pending_email(request, user):
    """Reenvía lo que le falta a una cuenta pendiente: la confirmación o la invitación."""
    if user.is_pending_registration:
        return send_verification_email(request, user)
    if user.is_invitation_pending:
        return send_invitation_email(request, user)
    return False


# ─── Quién está pendiente y límites ───────────────────────────────────────────

def email_budget_ok(email):
    """Cuenta un correo más hacia esta dirección y dice si todavía está dentro del límite por hora.

    `ratelimit.hit` falla ABIERTO si la caché cae (no bloquea a nadie y lo registra), igual que el registro.
    """
    return hit('verification-email', (email or '').strip().casefold(), EMAIL_SEND_LIMIT,
               EMAIL_SEND_WINDOW_SECONDS)


def accounts_with_email(email, limit=5):
    """Cuentas (no eliminadas) con ese correo, la más reciente primero. El correo no es único en la base."""
    return list(User.objects.filter(email__iexact=(email or '').strip(), erased_at__isnull=True)
                .order_by('-pk')[:limit])


def pending_account_for_email(email):
    """La cuenta pendiente (registro sin confirmar o invitación sin aceptar) con ese correo, o None."""
    for user in accounts_with_email(email):
        if user.is_pending_registration or user.is_invitation_pending:
            return user
    return None


def resend_for_email(request, email):
    """«Reenviar el correo»: si hay una cuenta pendiente con ese correo, le reenvía lo que le falta.

    No dice nada de lo que encontró (quien llama responde igual siempre) y respeta el límite por correo.
    """
    if not email_budget_ok(email):
        return False
    user = pending_account_for_email(email)
    if user is None:
        return False
    return send_pending_email(request, user)


def handle_register_with_existing_email(request, email):
    """Alguien se registró con un correo que ya existe. No crea nada ni dice que existe: a la dueña del
    correo le llega lo que le sirve (la confirmación si seguía pendiente; si no, un aviso con los pasos
    para entrar o recuperar la contraseña)."""
    if not email_budget_ok(email):
        return False
    users = accounts_with_email(email)
    if not users:
        return False
    for user in users:
        if user.is_pending_registration or user.is_invitation_pending:
            return send_pending_email(request, user)
    return send_existing_account_notice(request, users[0])


def pending_login_attempt(request, username, password):
    """¿Esta contraseña correcta es de una cuenta registrada que todavía no confirmó su correo?

    Solo se llama cuando `authenticate` ya falló (una cuenta inactiva nunca autentica). Se salta si
    django-axes bloqueó esta petición, para no usar el mensaje como oráculo de contraseñas durante un
    bloqueo. Búsqueda exacta, igual que `ModelBackend`.
    """
    if getattr(request, 'axes_locked_out', False) or not username or not password:
        return False
    user = User.objects.filter(username=username).first()
    if user is None or not user.is_pending_registration:
        return False
    return user.check_password(password)
