from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
import logging
import urllib.parse
import uuid

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.mail import BadHeaderError, send_mail
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.contrib.auth.hashers import make_password
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from . import choices
from . import email_verification
from . import services as account_services
from . import turnstile
from .axes_utils import get_client_ip
from .decorators import trainer_required, superuser_required
from .form_utils import errors_dict, flash_errors
from .forms import (
    DeactivateAccountForm, EraseAccountForm, MemberProfileForm, RegisterForm, TrainerAddClientForm,
    TrainerClientEditForm, TrainerCreateTrainerForm, TrainerDeactivateForm, TrainerEditForm,
    TrainerEraseForm, TransferAdminForm,
)
from .models import User
from .permissions import clients_for_trainer, get_client_for_trainer, reject_if_inactive
from .ratelimit import hit
from .pagination import pagination_context

logger = logging.getLogger(__name__)

# Campos que cada plantilla ya pinta junto al input (`errors.<campo>`): el resto de
# errores se avisa además con `messages` para que nunca fallen en silencio.
# Cada tupla se verificó contra su plantilla con `errors.<campo>` (register.html,
# client_add.html, client_edit.html, profile_edit.html, trainer_form.html, trainer_edit.html).
# Si una plantilla deja de pintar un campo, hay que quitarlo de aquí para que vuelva el aviso.
REGISTER_RENDERED = ('first_name', 'last_name', 'email', 'phone', 'username', 'password1',
                     'password2', 'gender', 'training_goal', 'captcha')
CLIENT_ADD_RENDERED = ('first_name', 'last_name', 'email', 'phone', 'username', 'gender')
CLIENT_EDIT_RENDERED = ('first_name', 'last_name', 'email', 'phone', 'username', 'new_password',
                        'gender')
PROFILE_RENDERED = ('first_name', 'last_name', 'email', 'phone', 'gender', 'bio', 'profile_photo',
                    'current_password', 'new_password', 'confirm_password')
TRAINER_FORM_RENDERED = ('first_name', 'last_name', 'email', 'phone', 'username', 'password',
                         'password2', 'bio')
TRAINER_EDIT_RENDERED = ('first_name', 'last_name', 'email', 'phone', 'bio', 'profile_photo')
# Confirmaciones de cuentas (C-03, C-04, C-06, C-07). Los esqueletos de C-03, C-06 y C-07 pintan
# `errors.<campo>`; la plantilla vieja de C-04 (client_confirm_delete.html) todavía no, por eso
# su tupla está vacía: cuando frontend-architect la entregue, agregar los campos que pinte.
CLIENT_DEACTIVATE_RENDERED = ('reason',)
CLIENT_ERASE_RENDERED = ('confirm_name', 'reason', 'ack_payments')
TRAINER_DEACTIVATE_RENDERED = ('reason', 'reassign_to')
TRAINER_ERASE_RENDERED = ('confirm_name', 'reason', 'reassign_to', 'ack_payments')
# Transferir la administración (docs/contracts/transferir-administracion.md): el esqueleto pinta los tres.
ADMIN_TRANSFER_RENDERED = ('new_admin', 'password', 'confirm')

# Límites del registro público (por IP, ventana fija). Se pueden ajustar en settings.
REGISTER_ATTEMPTS_LIMIT = getattr(settings, 'REGISTER_ATTEMPTS_LIMIT', 20)    # envíos de formulario / ventana
REGISTER_SUCCESS_LIMIT = getattr(settings, 'REGISTER_SUCCESS_LIMIT', 5)       # cuentas creadas / ventana
REGISTER_WINDOW_SECONDS = getattr(settings, 'REGISTER_WINDOW_SECONDS', 3600)

# Límite de intentos al transferir la administración (por cuenta que entrega, ventana fija): frena a
# quien tenga una sesión robada e intente adivinar la contraseña. Se puede ajustar en settings.
TRANSFER_ADMIN_ATTEMPTS_LIMIT = getattr(settings, 'TRANSFER_ADMIN_ATTEMPTS_LIMIT', 5)
TRANSFER_ADMIN_WINDOW_SECONDS = getattr(settings, 'TRANSFER_ADMIN_WINDOW_SECONDS', 900)


PASSWORD_FIELDS = ('password', 'password1', 'password2', 'current_password',
                   'new_password', 'confirm_password', 'new_password1', 'new_password2')


def _safe_form_data(data):
    """Conserva campos corregibles; las claves nunca vuelven al contexto ni al HTML."""
    safe = data.copy()
    for field in PASSWORD_FIELDS:
        safe.pop(field, None)
    return safe


PENDING_LOGIN_MESSAGE = ('Tu cuenta todavía no está activa: falta confirmar tu correo. Abre el enlace del correo '
                         'que te enviamos o pide que te lo enviemos de nuevo.')


@sensitive_post_parameters(*PASSWORD_FIELDS)
@sensitive_variables()
def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    email_pending = False   # contraseña correcta de una cuenta registrada que aún no confirma su correo
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)

        if user:
            login(request, user)
            if request.POST.get('remember_me'):
                request.session.set_expiry(864000)  # 10 días en segundos
            else:
                request.session.set_expiry(0)  # Expira al cerrar el navegador
            # Solo se acepta un 'next' interno (evita redirección abierta); puede
            # venir en el formulario (POST) o en la URL (GET).
            next_url = request.POST.get('next') or request.GET.get('next') or ''
            if next_url and url_has_allowed_host_and_scheme(
                url=next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            return redirect('dashboard')
        elif email_verification.pending_login_attempt(request, username, password):
            # Solo con la contraseña correcta se dice que falta confirmar: no revela nada que el login
            # normal no revele ya (una cuenta inactiva nunca autentica, así que `authenticate` dio None)
            email_pending = True
            messages.warning(request, PENDING_LOGIN_MESSAGE)
        else:
            messages.error(request, 'Usuario o contraseña incorrectos.')

    return render(request, 'accounts/login.html', {
        'email_pending': email_pending,
        'resend_url': reverse('verification_resend'),
    })


@require_POST
def logout_view(request):
    logout(request)
    return redirect('home')


@login_required
def dashboard(request):
    if request.user.is_trainer:
        return redirect('trainer_dashboard')

    try:
        membership = request.user.membership
    except ObjectDoesNotExist:
        return render(request, 'accounts/no_membership.html')
    if not membership.is_valid:
        return render(request, 'accounts/membership_expired.html', {
            'membership': membership,
        })

    routines = request.user.routines.filter(is_active=True).select_related(
        'plan__approved_by'
    ).prefetch_related('days__exercises__exercise')
    from apps.plans.journey import client_journey
    from apps.whatsapp import conf as whatsapp_conf
    from apps.whatsapp import services as whatsapp_services
    return render(request, 'accounts/member_dashboard.html', {
        'membership': membership,
        'routines': routines,
        # Camino guiado hacia el plan con IA (docs/contracts/camino-guiado.md): None cuando no aplica
        'journey': client_journey(request.user),
        # Avisos por WhatsApp (W-03): None con la función apagada; si no, 'off', 'pending', 'active',
        # 'wrong_number', 'undeliverable' u 'opted_out'
        'whatsapp_status': (whatsapp_services.status_for(request.user)
                            if whatsapp_conf.enabled() else None),
    })


@trainer_required
def trainer_dashboard(request):
    from apps.accounts.models import User
    from apps.memberships.models import Membership
    from apps.exercises.models import Exercise
    from apps.routines.models import Routine
    from apps.assessments.models import InitialAssessment

    today = timezone.localdate()
    is_super = request.user.is_superuser

    # Superusuaria ve todo; los demás entrenadores solo sus clientes asignados (las desactivadas
    # no cuentan en el resumen: se ven en la lista con el filtro «Desactivadas»)
    my_clients_qs = clients_for_trainer(request.user).filter(is_active=True)

    my_client_ids = my_clients_qs.values_list('pk', flat=True)

    context = {
        'total_clients': my_clients_qs.count(),
        'active_memberships': Membership.objects.filter(
            is_active=True, end_date__gte=today, user_id__in=my_client_ids
        ).count(),
        'expiring_soon': Membership.objects.filter(
            is_active=True,
            end_date__gte=today,
            end_date__lte=today + timezone.timedelta(days=7),
            user_id__in=my_client_ids,
        ).select_related('user'),
        'total_exercises': Exercise.objects.filter(is_active=True).count(),
        'total_routines': Routine.objects.filter(
            is_active=True, user_id__in=my_client_ids
        ).count(),
        'recent_clients': my_clients_qs.order_by('-date_joined')[:6],
        'recent_assessments': InitialAssessment.objects.filter(
            user_id__in=my_client_ids
        ).select_related('user').order_by('-date')[:5],
        'is_superuser': is_super,
        # Clientes nuevos sin entrenador asignado (solo superusuario los ve)
        'unassigned_clients': User.objects.filter(
            role='member', assigned_trainer__isnull=True, is_active=True, erased_at__isnull=True,
        ).count() if is_super else 0,
    }
    return render(request, 'trainer/dashboard.html', context)


def _notify_new_registration(user, plan):
    """Avisa por correo a la(s) superusuaria(s). Un fallo de correo nunca rompe el registro."""
    try:
        superuser_emails = list(
            User.objects.filter(is_superuser=True, is_active=True).exclude(email='')
            .values_list('email', flat=True)
        )
        if not superuser_emails:
            return
        plan_txt = plan.name if plan else 'Sin plan'
        goal_txt = dict(User.GOAL_CHOICES).get(user.training_goal, 'No indicado')
        send_mail(
            subject=f'🏋️ Nuevo registro: {user.get_full_name() or user.username}',
            message=(
                'Nueva inscripción en ProFit Studio:\n\n'
                f'Nombre: {user.get_full_name()}\n'
                f'Usuario: {user.username}\n'
                f'Email: {user.email}\n'
                f'Teléfono: {user.phone or "No indicado"}\n'
                f'Plan de interés: {plan_txt}\n'
                f'Objetivo: {goal_txt}\n\n'
                'Accede al panel para asignarle entrenadora y membresía.'
            ),
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@profitstudio.com'),
            recipient_list=superuser_emails,
            fail_silently=True,
        )
    except (BadHeaderError, OSError):
        logger.exception('No se pudo enviar el aviso de nuevo registro (usuario %s).', user.pk)


def _register_context(plans, selected_plan_id, errors, form):
    return {
        **turnstile.widget_context(),   # clave pública del widget ('' si Turnstile está apagado)
        'plans': plans,
        'selected_plan_id': selected_plan_id,
        'goal_choices': User.GOAL_CHOICES,
        'errors': errors,
        'form': _safe_form_data(form),
    }


def _register_rate_limited(request, plans, selected_plan_id):
    messages.error(
        request,
        'Hemos recibido demasiados intentos de registro desde tu conexión. '
        'Espera un rato e inténtalo de nuevo, o escríbenos por WhatsApp.',
    )
    response = render(request, 'accounts/register.html',
                      _register_context(plans, selected_plan_id, {}, request.POST), status=429)
    response['Retry-After'] = str(REGISTER_WINDOW_SECONDS)
    return response


@sensitive_post_parameters(*PASSWORD_FIELDS)
@sensitive_variables()
def register_view(request):
    """Auto-registro público: cualquier visitante puede crear su cuenta de miembro.

    Protecciones: validación con `RegisterForm` (incluye `validate_password`), límite de
    frecuencia por IP (envíos y cuentas creadas por hora) y Turnstile si está configurado.

    Verificación del correo (docs/contracts/verificacion-correo.md): la cuenta nace INACTIVA y
    sin `email_verified_at`; se envía un correo con un enlace firmado de 24 horas y se activa al
    confirmarlo (`email_verify_confirm`). La respuesta es la misma exista o no el correo: siempre
    la pantalla «Revisa tu correo». Si el correo ya tenía cuenta no se crea nada y la dueña del
    correo recibe el aviso o la confirmación pendiente. El aviso a la superusuaria sale al
    confirmar, no aquí.
    """
    if request.user.is_authenticated:
        return redirect('dashboard')

    from apps.memberships.models import MembershipPlan
    plans = MembershipPlan.objects.filter(is_active=True).order_by('duration_days')

    if request.method != 'POST':
        selected_plan_id = request.GET.get('plan') or None
        return render(request, 'accounts/register.html', _register_context(plans, selected_plan_id, {}, {}))

    ip = get_client_ip(request) or 'desconocida'
    plan_id = request.POST.get('interested_plan') or None
    if not hit('register-attempt', ip, REGISTER_ATTEMPTS_LIMIT, REGISTER_WINDOW_SECONDS):
        return _register_rate_limited(request, plans, plan_id)

    # Turnstile (solo si hay claves): antes de validar nada, para no gastar trabajo en un robot
    if not turnstile.verify(request.POST.get(turnstile.FORM_FIELD), ip):
        return render(request, 'accounts/register.html',
                      _register_context(plans, plan_id, {'captcha': turnstile.MESSAGE}, request.POST))

    form = RegisterForm(request.POST)
    if form.is_valid():
        # Solo las cuentas realmente creadas cuentan para el límite estricto, así un error de
        # tecleo no bloquea a una persona legítima. También cuenta cuando el correo ya existía:
        # el límite no puede delatar si existe.
        if not hit('register-success', ip, REGISTER_SUCCESS_LIMIT, REGISTER_WINDOW_SECONDS):
            return _register_rate_limited(request, plans, plan_id)
        data = form.cleaned_data
        if email_verification.accounts_with_email(data['email'], limit=1):
            # Correo ya registrado: no se crea nada. Se calcula un hash igual de lento que el de crear
            # la cuenta para que el tiempo de respuesta tampoco delate que existe.
            make_password(data['password1'])
            email_verification.handle_register_with_existing_email(request, data['email'])
            return _registered(request, data['email'])
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=data['username'],
                    email=data['email'],
                    password=data['password1'],
                    first_name=data['first_name'],
                    last_name=data['last_name'],
                    phone=data['phone'],
                    gender=data['gender'],
                    role=User.ROLE_MEMBER,
                    interested_plan=data['interested_plan'],
                    training_goal=data['training_goal'],
                    is_active=False,    # se activa al confirmar el correo
                )
        except IntegrityError:   # carrera: otro registro tomó el mismo usuario justo ahora
            form.add_error('username', form.username_taken_message)
        else:
            if email_verification.email_budget_ok(user.email):
                email_verification.send_verification_email(request, user)
            return _registered(request, data['email'])

    errors = errors_dict(form)
    flash_errors(request, form, errors, skip=REGISTER_RENDERED)
    return render(request, 'accounts/register.html', _register_context(plans, plan_id, errors, request.POST))


def _registered(request, email):
    """Respuesta única del registro: «Revisa tu correo». El correo escrito se recuerda en la sesión
    solo para repetírselo a la persona (es lo que ella tecleó; no confirma nada)."""
    request.session['register_email'] = email
    return redirect('register_check_email')


def register_success(request):
    if not request.user.is_authenticated:
        return redirect('register')
    from django.conf import settings
    plan = request.user.interested_plan
    whatsapp_number = getattr(settings, 'WHATSAPP_NUMBER', '').replace('+', '').replace(' ', '')
    msg = f'Hola! Me registré en ProFit Studio. Mi usuario es *{request.user.username}*'
    if plan:
        msg += f' y me interesa el plan *{plan.name}*'
    msg += '. ¿Qué sigue para activar mi acceso?'
    wa_link = f'https://wa.me/{whatsapp_number}?text={urllib.parse.quote(msg)}'
    return render(request, 'accounts/register_success.html', {
        'plan': plan,
        'wa_link': wa_link,
    })


@trainer_required
@sensitive_post_parameters(*PASSWORD_FIELDS)
@sensitive_variables()
def trainer_add_client(request):
    """La entrenadora crea una cuenta de cliente y opcionalmente activa su membresía.

    Ya no fija la contraseña (docs/contracts/verificacion-correo.md): la cuenta nace activa con una
    contraseña inutilizable y la clienta recibe por correo una INVITACIÓN (enlace firmado de 72 horas)
    para crear la suya; al hacerlo queda con el correo verificado. Mientras tanto la entrenadora la ve
    marcada «invitación pendiente» y puede asignarle membresía, valoraciones, etc.
    """
    from apps.memberships.models import MembershipPlan, Membership
    plans = MembershipPlan.objects.filter(is_active=True).order_by('duration_days')

    if request.method == 'POST':
        form = TrainerAddClientForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                with transaction.atomic():
                    client = User.objects.create_user(
                        username=data['username'], email=data['email'], password=None,
                        first_name=data['first_name'], last_name=data['last_name'],
                        phone=data['phone'], role=User.ROLE_MEMBER, gender=data['gender'],
                        # Asignación automática: el cliente queda a cargo de quien lo crea
                        assigned_trainer=request.user,
                    )
            except IntegrityError:   # carrera con otra creación del mismo usuario
                form.add_error('username', form.username_taken_message)
            else:
                invited = email_verification.send_invitation_email(request, client)
                plan = data['activate_plan']
                if plan:
                    membership = Membership(user=client)
                    membership.activate(plan, plan.duration_days, request.user)
                    messages.success(
                        request,
                        f'Cliente {client.get_full_name()} creado y membresía "{plan.name}" activada.'
                    )
                else:
                    messages.success(request, f'Cliente {client.get_full_name()} creado. Recuerda activar su membresía.')
                if invited:
                    messages.info(request, f'Enviamos una invitación a {client.email} para que cree su contraseña.')
                else:
                    messages.warning(request, 'No pudimos enviar la invitación por correo. Usa «Reenviar invitación» '
                                              'en la ficha de la clienta cuando el correo funcione.')
                return redirect('trainer_client_detail', pk=client.pk)

        errors = errors_dict(form)
        flash_errors(request, form, errors, skip=CLIENT_ADD_RENDERED)
        return render(request, 'trainer/client_add.html', {
            'plans': plans,
            'errors': errors,
            'form': _safe_form_data(request.POST),
        })

    return render(request, 'trainer/client_add.html', {
        'plans': plans,
        'errors': {},
        'form': {},
    })


def _list_status(request):
    """`?status=active|inactive|all`; sin valor o con uno no válido, `active`."""
    status = request.GET.get('status', '')
    return status if status in choices.STATUS_CHOICES else choices.STATUS_ACTIVE


def _filter_status(queryset, status):
    if status == choices.STATUS_ACTIVE:
        return queryset.filter(is_active=True)
    if status == choices.STATUS_INACTIVE:
        return queryset.filter(is_active=False)
    return queryset


def _status_counts(queryset):
    counts = queryset.aggregate(active=Count('pk', filter=Q(is_active=True)),
                                inactive=Count('pk', filter=Q(is_active=False)))
    counts['all'] = counts['active'] + counts['inactive']
    return counts


@trainer_required
def trainer_client_list(request):
    is_super = request.user.is_superuser
    status = _list_status(request)
    accessible = clients_for_trainer(request.user)
    clients = _filter_status(accessible, status).select_related('assigned_trainer', 'membership').order_by(
        'first_name', 'last_name', 'pk')
    page, pagination = pagination_context(request, clients, ('status',))
    unassigned_count = (accessible.filter(is_active=True, assigned_trainer__isnull=True).count()
                        if is_super else 0)
    return render(request, 'trainer/clients.html', {
        'clients': page.object_list,
        **pagination,
        'is_superuser': is_super,
        'unassigned_count': unassigned_count,
        'status': status,
        'counts': _status_counts(accessible),
    })


@trainer_required
def trainer_client_detail(request, pk):
    client = get_client_for_trainer(request, pk)
    try:
        membership = client.membership
    except ObjectDoesNotExist:
        membership = None
    assessments = client.assessments.select_related('dixon_test').order_by('-date')
    routines = client.routines.filter(is_active=True).prefetch_related('days__exercises__exercise')
    return render(request, 'trainer/client_detail.html', {
        'client': client,
        'membership': membership,
        'assessments': assessments,
        'routines': routines,
        'plan_request_token': uuid.uuid4(),   # botón "Generar borrador de rutina con IA"
        'plan_request_token_nutrition': uuid.uuid4(),   # botón "Generar borrador de alimentación con IA"
        # Cuentas (C-02): qué botones se muestran
        'can_deactivate': client.is_active and account_services.can_act(
            request.user, client, choices.ACTION_DEACTIVATE),
        'can_reactivate': not client.is_active and account_services.can_act(
            request.user, client, choices.ACTION_REACTIVATE),
        'can_erase': account_services.can_act(request.user, client, choices.ACTION_ERASE),
    })


@trainer_required
@sensitive_post_parameters(*PASSWORD_FIELDS)
@sensitive_variables()
def trainer_client_edit(request, pk):
    client = get_client_for_trainer(request, pk)
    blocked = reject_if_inactive(request, client)
    if blocked:
        return blocked
    from apps.memberships.models import MembershipPlan
    plans = MembershipPlan.objects.filter(is_active=True).order_by('duration_days')

    if request.method == 'POST':
        form = TrainerClientEditForm(request.POST, client=client)
        if form.is_valid():
            data = form.cleaned_data
            # Si cambia el correo de una invitación pendiente, el enlace anterior ya no sirve (quedó en otro
            # buzón): se envía una invitación nueva al correo nuevo.
            reinvite = client.is_invitation_pending and data['email'].lower() != (client.email or '').lower()
            client.first_name = data['first_name']
            client.last_name = data['last_name']
            client.email = data['email']
            client.username = data['username']
            client.phone = data['phone']
            client.gender = data['gender']
            client.interested_plan = data['interested_plan']
            if data['new_password']:
                client.set_password(data['new_password'])
            try:
                with transaction.atomic():
                    client.save()
            except IntegrityError:   # carrera con otro cambio de usuario
                form.add_error('username', form.username_taken_message)
            else:
                messages.success(request, f'Datos de {client.get_full_name()} actualizados correctamente.')
                if reinvite:
                    if email_verification.send_invitation_email(request, client):
                        messages.info(request, f'Como cambió el correo, enviamos una invitación nueva a {client.email}.')
                    else:
                        messages.warning(request, 'Cambió el correo pero no pudimos enviar la invitación. '
                                                  'Usa «Reenviar invitación» en la ficha de la clienta.')
                return redirect('trainer_client_detail', pk=client.pk)

        errors = errors_dict(form)
        flash_errors(request, form, errors, skip=CLIENT_EDIT_RENDERED)
        return render(request, 'trainer/client_edit.html', {
            'client': get_client_for_trainer(request, pk),   # se relee: sin datos a medio editar
            'plans': plans,
            'errors': errors,
            'form': _safe_form_data(request.POST),
        })

    return render(request, 'trainer/client_edit.html', {
        'client': client,
        'plans': plans,
        'errors': {},
        'form': {
            'first_name': client.first_name,
            'last_name': client.last_name,
            'email': client.email,
            'username': client.username,
            'phone': client.phone,
            'gender': client.gender,
            'interested_plan': str(client.interested_plan_id) if client.interested_plan_id else '',
        },
    })


# ─── Desactivar, reactivar y eliminar cuentas (docs/contracts/cuentas-eliminar.md) ─────────

MSG_ALREADY_INACTIVE = 'Esa cuenta ya estaba desactivada.'
MSG_ALREADY_ACTIVE = 'Esa cuenta ya estaba activa.'


def _reasons():
    return list(choices.REASON_CHOICES)


def _client_target(request, pk, action):
    """La clienta `pk` si quien pide puede hacer `action` sobre ella; si no, 404 (no se revela)."""
    client = get_client_for_trainer(request, pk)
    if not account_services.can_act(request.user, client, action):
        raise Http404
    return client


def _trainer_target(request, pk, action):
    """El entrenador `pk` (nunca una superusuaria, una cuenta eliminada ni la propia); si no, 404."""
    trainer = get_object_or_404(User, pk=pk, role=User.ROLE_TRAINER, is_superuser=False,
                                erased_at__isnull=True)
    if not account_services.can_act(request.user, trainer, action):
        raise Http404
    return trainer


def _run_action(request, func, *args, **kwargs):
    """Ejecuta el caso de uso y devuelve `(resultado, errores_de_campo)`.

    - Bien: `(resultado, {})`.
    - Error de un campo detectado en la transacción (p. ej. el destino se desactivó): `(None, {campo: texto})`.
    - Rechazo o fallo inesperado: `(None, None)` con el mensaje ya puesto. La traza va al logger,
      nunca a la pantalla.
    """
    try:
        return func(*args, **kwargs), {}
    except account_services.AccountFieldError as exc:
        return None, {exc.field: exc.message}
    except account_services.AccountActionError as exc:
        logger.warning('account action rejected code=%s actor_id=%s', exc.code, request.user.pk)
        messages.error(request, exc.message)
        return None, None
    except Exception:
        logger.exception('account action failed actor_id=%s', request.user.pk)
        messages.error(request, account_services.GENERIC_ERROR)
        return None, None


def _render_with_errors(request, template, context, form, errors, rendered):
    flash_errors(request, form, errors, skip=rendered)
    context.update({'errors': errors, 'form': request.POST})
    return render(request, template, context)


@require_http_methods(['GET', 'POST'])
@never_cache
@trainer_required
def trainer_client_deactivate(request, pk):
    """C-03. El entrenador (sus clientas) o la superusuaria (todas) desactiva una cuenta."""
    client = _client_target(request, pk, choices.ACTION_DEACTIVATE)
    context = {'client': client, 'reasons': _reasons()}
    if request.method == 'POST':
        form = DeactivateAccountForm(request.POST)
        errors = {} if form.is_valid() else errors_dict(form)
        if not errors:
            name = client.display_name
            result, errors = _run_action(request, account_services.deactivate_account, client,
                                         request.user, form.cleaned_data['reason'])
            if result is not None:
                if result.changed:
                    messages.success(request, f'Desactivaste la cuenta de {name}. Ya no puede iniciar sesión '
                                              'y su historial se conserva. Puedes reactivarla cuando quieras.')
                else:
                    messages.info(request, MSG_ALREADY_INACTIVE)
            if errors is None or result is not None:
                return redirect('trainer_client_detail', pk=client.pk)
        return _render_with_errors(request, 'trainer/client_deactivate.html', context, form, errors,
                                   CLIENT_DEACTIVATE_RENDERED)
    context.update({'errors': {}, 'form': {}})
    return render(request, 'trainer/client_deactivate.html', context)


@require_POST
@trainer_required
def trainer_client_reactivate(request, pk):
    client = _client_target(request, pk, choices.ACTION_REACTIVATE)
    name = client.display_name
    result, _errors = _run_action(request, account_services.reactivate_account, client, request.user)
    if result is not None:
        if result.changed:
            messages.success(request, f'La cuenta de {name} está activa de nuevo.')
        else:
            messages.info(request, MSG_ALREADY_ACTIVE)
    return redirect('trainer_client_detail', pk=client.pk)


@require_http_methods(['GET', 'POST'])
@never_cache
@superuser_required
def trainer_client_delete(request, pk):
    """C-04. Eliminar definitivamente una clienta: solo la superusuaria (respuesta 2a)."""
    client = _client_target(request, pk, choices.ACTION_ERASE)
    display_name = client.display_name   # se toma ANTES de eliminar
    context = {'client': client, 'display_name': display_name, 'reasons': _reasons(),
               **account_services.client_erase_context(client)}
    if request.method == 'POST':
        form = EraseAccountForm(request.POST, expected_name=display_name, needs_ack=context['needs_ack'])
        errors = {} if form.is_valid() else errors_dict(form)
        if not errors:
            result, errors = _run_action(request, account_services.erase_account, client, request.user,
                                         form.cleaned_data['reason'])
            if result is not None:
                messages.success(request, f'Eliminaste definitivamente la cuenta de {display_name}. '
                                          'Sus pagos se conservan sin datos personales.')
                return redirect('trainer_clients')
            if errors is None:
                return redirect('trainer_client_detail', pk=client.pk)
        return _render_with_errors(request, 'trainer/client_confirm_delete.html', context, form, errors,
                                   CLIENT_ERASE_RENDERED)
    context.update({'errors': {}, 'form': {}})
    return render(request, 'trainer/client_confirm_delete.html', context)


@login_required
@sensitive_post_parameters(*PASSWORD_FIELDS)
@sensitive_variables()
def member_profile_edit(request):
    user = request.user

    if request.method == 'POST':
        form = MemberProfileForm(request.POST, request.FILES, user=user)
        if form.is_valid():
            data = form.cleaned_data
            user.first_name = data['first_name']
            user.last_name = data['last_name']
            user.email = data['email']
            user.phone = data['phone']
            user.gender = data['gender']
            if user.is_trainer:
                user.bio = data['bio']
            if data['profile_photo']:
                user.profile_photo = data['profile_photo']
            if data['new_password']:
                user.set_password(data['new_password'])
            user.save()
            if data['new_password']:
                # set_password invalida la sesión actual: se vuelve a iniciar para no sacar a la persona
                login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            messages.success(request, 'Tu perfil ha sido actualizado correctamente.')
            return redirect('dashboard')

        errors = errors_dict(form)
        flash_errors(request, form, errors, skip=PROFILE_RENDERED)
        return render(request, 'accounts/profile_edit.html', {
            'errors': errors,
            'form': _safe_form_data(request.POST),
        })

    return render(request, 'accounts/profile_edit.html', {
        'errors': {},
        'form': {
            'first_name': user.first_name,
            'last_name': user.last_name,
            'email': user.email,
            'phone': user.phone,
            'gender': user.gender,
            'bio': user.bio,
        },
    })


# ── Solo Yiseth (superusuario) puede crear nuevos entrenadores ──────────────────
@superuser_required
@sensitive_post_parameters(*PASSWORD_FIELDS)
@sensitive_variables()
def trainer_create_trainer(request):
    """Vista exclusiva del superusuario para crear cuentas de entrenadores."""
    if request.method == 'POST':
        form = TrainerCreateTrainerForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                with transaction.atomic():
                    trainer = User.objects.create_user(
                        username=data['username'],
                        email=data['email'],
                        password=data['password'],
                        first_name=data['first_name'],
                        last_name=data['last_name'],
                        phone=data['phone'],
                        bio=data['bio'],
                        role=User.ROLE_TRAINER,
                        is_staff=True,
                        is_superuser=False,  # Solo Yiseth es superusuaria
                        # La superusuaria fija su contraseña y le avisa: no pasan por la verificación
                        email_verified_at=timezone.now(),
                    )
            except IntegrityError:   # carrera con otra creación del mismo usuario
                form.add_error('username', form.username_taken_message)
            else:
                messages.success(
                    request,
                    f'Entrenador/a {trainer.get_full_name()} creado/a correctamente. '
                    f'Usuario: {trainer.username} — Deberá cambiar su contraseña al ingresar.'
                )
                return redirect('trainer_staff_list')

        errors = errors_dict(form)
        flash_errors(request, form, errors, skip=TRAINER_FORM_RENDERED)
        return render(request, 'trainer/trainer_form.html', {
            'errors': errors,
            'form': _safe_form_data(request.POST),
        })

    return render(request, 'trainer/trainer_form.html', {'errors': {}, 'form': {}})


@superuser_required
def trainer_staff_list(request):
    """Lista de entrenadores — solo superusuario. `?status=active|inactive|all` (C-05)."""
    status = _list_status(request)
    staff = User.objects.filter(role=User.ROLE_TRAINER, erased_at__isnull=True)
    trainers = _filter_status(staff, status).annotate(
        client_count=Count('assigned_clients', distinct=True),
    ).order_by('-is_superuser', 'first_name', 'last_name')
    return render(request, 'trainer/trainer_list.html', {
        'trainers': trainers,
        'status': status,
        'counts': _status_counts(staff),
    })


def _trainer_action_context(trainer):
    return {'trainer_obj': trainer, 'reasons': _reasons(),
            **account_services.trainer_action_context(trainer)}


def _reassign_message(result):
    if result.reassigned_count:
        return f' {result.reassigned_count} clientas pasaron a {result.reassigned_to.display_name}.'
    return ''


@require_http_methods(['GET', 'POST'])
@never_cache
@superuser_required
def trainer_staff_deactivate(request, pk):
    """C-06. Desactiva un entrenador y pasa sus clientas a otro (en la misma transacción)."""
    trainer = _trainer_target(request, pk, choices.ACTION_DEACTIVATE)
    context = _trainer_action_context(trainer)
    if request.method == 'POST':
        form = TrainerDeactivateForm(request.POST, client_count=context['client_count'],
                                     targets=account_services.valid_reassign_targets(trainer))
        errors = {} if form.is_valid() else errors_dict(form)
        if not errors:
            name = trainer.display_name
            result, errors = _run_action(request, account_services.deactivate_account, trainer, request.user,
                                         form.cleaned_data['reason'], form.cleaned_data['reassign_to'])
            if result is not None:
                if result.changed:
                    messages.success(request, f'Desactivaste a {name}.{_reassign_message(result)}')
                else:
                    messages.info(request, MSG_ALREADY_INACTIVE)
            if errors is None or result is not None:
                return redirect('trainer_staff_list')
            context = _trainer_action_context(trainer)   # el destino pudo cambiar: se relee la lista
        return _render_with_errors(request, 'trainer/trainer_deactivate.html', context, form, errors,
                                   TRAINER_DEACTIVATE_RENDERED)
    context.update({'errors': {}, 'form': {}})
    return render(request, 'trainer/trainer_deactivate.html', context)


@require_POST
@superuser_required
def trainer_staff_reactivate(request, pk):
    trainer = _trainer_target(request, pk, choices.ACTION_REACTIVATE)
    name = trainer.display_name
    result, _errors = _run_action(request, account_services.reactivate_account, trainer, request.user)
    if result is not None:
        if result.changed:
            messages.success(request, f'{name} está activo/a de nuevo. Sus clientas anteriores siguen con '
                                      'quien las tiene ahora; reasígnalas si hace falta.')
        else:
            messages.info(request, MSG_ALREADY_ACTIVE)
    return redirect('trainer_staff_list')


@require_http_methods(['GET', 'POST'])
@never_cache
@superuser_required
def trainer_staff_delete(request, pk):
    """C-07. Elimina definitivamente un entrenador (lápida) y pasa sus clientas a otro."""
    trainer = _trainer_target(request, pk, choices.ACTION_ERASE)
    display_name = trainer.display_name   # se toma ANTES de eliminar
    context = {**_trainer_action_context(trainer), 'display_name': display_name,
               'validated_foods_hint': account_services.validated_foods_hint(trainer)}
    if request.method == 'POST':
        form = TrainerEraseForm(request.POST, expected_name=display_name,
                                client_count=context['client_count'],
                                targets=account_services.valid_reassign_targets(trainer))
        errors = {} if form.is_valid() else errors_dict(form)
        if not errors:
            result, errors = _run_action(request, account_services.erase_account, trainer, request.user,
                                         form.cleaned_data['reason'], form.cleaned_data['reassign_to'])
            if result is not None:
                messages.success(request, f'Eliminaste definitivamente la cuenta de {display_name}.'
                                          f'{_reassign_message(result)}')
                return redirect('trainer_staff_list')
            if errors is None:
                return redirect('trainer_staff_list')
            context.update(_trainer_action_context(trainer))
        return _render_with_errors(request, 'trainer/trainer_confirm_delete.html', context, form, errors,
                                   TRAINER_ERASE_RENDERED)
    context.update({'errors': {}, 'form': {}})
    return render(request, 'trainer/trainer_confirm_delete.html', context)


# ─── Transferir la administración (docs/contracts/transferir-administracion.md) ──────────────

def _transfer_context(actor, candidates):
    return {
        'targets': list(candidates),
        'kept_clients_count': User.objects.filter(assigned_trainer=actor, erased_at__isnull=True).count(),
    }


def _render_transfer(request, candidates, errors, form_data, status=200):
    """Vuelve a pintar el formulario. La contraseña nunca llega a la plantilla (`form_data` no la trae)."""
    context = _transfer_context(request.user, candidates)
    context.update({'errors': errors, 'form': form_data})
    return render(request, 'trainer/admin_transfer.html', context, status=status)


@require_http_methods(['GET', 'POST'])
@never_cache
@superuser_required
@sensitive_post_parameters(*PASSWORD_FIELDS)
@sensitive_variables()
def trainer_admin_transfer(request):
    """Transferir la administración: la superusuaria se la entrega a otro entrenador activo y pasa a
    ser un entrenador común. GET muestra el formulario; POST transfiere (contraseña de nuevo + casilla)."""
    candidates = account_services.transfer_admin_candidates(request.user)
    if request.method != 'POST':
        return _render_transfer(request, candidates, {}, {})

    form = TransferAdminForm(request.POST, candidates=candidates)
    form_data = {'new_admin': request.POST.get('new_admin', '')}    # sin la contraseña
    errors = {} if form.is_valid() else errors_dict(form)
    if errors:
        flash_errors(request, form, errors, skip=ADMIN_TRANSFER_RENDERED)
        return _render_transfer(request, candidates, errors, form_data)

    if not hit('transfer-admin', request.user.pk, TRANSFER_ADMIN_ATTEMPTS_LIMIT,
               TRANSFER_ADMIN_WINDOW_SECONDS):
        logger.warning('admin transfer rate limited actor_id=%s', request.user.pk)
        messages.error(request, 'Demasiados intentos. Espera unos minutos e inténtalo de nuevo.')
        response = _render_transfer(request, candidates, {}, form_data, status=429)
        response['Retry-After'] = str(TRANSFER_ADMIN_WINDOW_SECONDS)
        return response

    new_admin = form.cleaned_data['new_admin']
    name = new_admin.display_name   # se toma ANTES de transferir
    result, errors = _run_action(request, account_services.transfer_admin, request.user, new_admin,
                                 form.cleaned_data['password'])
    if result is not None:
        messages.success(request, f'Traspasaste la administración a {name}. Sigues como entrenador/a '
                                  'con tus clientas, pero ya no ves Pagos ni Entrenadores.')
        return redirect('trainer_dashboard')
    if errors is None:   # rechazo o fallo inesperado: el mensaje ya está puesto
        return redirect('trainer_dashboard')
    flash_errors(request, form, errors, skip=ADMIN_TRANSFER_RENDERED)
    return _render_transfer(request, account_services.transfer_admin_candidates(request.user), errors, form_data)


@superuser_required
def trainer_edit_trainer(request, pk):
    """Editar datos de un entrenador — solo superusuario."""
    trainer = get_object_or_404(User, pk=pk, role='trainer', erased_at__isnull=True)

    if request.method == 'POST':
        form = TrainerEditForm(request.POST, request.FILES, trainer=trainer)
        if form.is_valid():
            data = form.cleaned_data
            trainer.first_name = data['first_name']
            trainer.last_name = data['last_name']
            trainer.email = data['email']
            trainer.phone = data['phone']
            trainer.bio = data['bio']
            if data['profile_photo']:
                trainer.profile_photo = data['profile_photo']
            trainer.save()
            messages.success(request, f'Perfil de {trainer.get_full_name()} actualizado correctamente.')
            return redirect('trainer_staff_list')

        errors = errors_dict(form)
        flash_errors(request, form, errors, skip=TRAINER_EDIT_RENDERED)
        return render(request, 'trainer/trainer_edit.html', {
            'trainer_obj': get_object_or_404(User, pk=pk, role='trainer', erased_at__isnull=True),
            'errors': errors,
            'form': _safe_form_data(request.POST),
        })

    return render(request, 'trainer/trainer_edit.html', {
        'trainer_obj': trainer,
        'errors': {},
        'form': {
            'first_name': trainer.first_name,
            'last_name':  trainer.last_name,
            'email':      trainer.email,
            'phone':      trainer.phone,
            'bio':        trainer.bio,
        },
    })


@superuser_required
def trainer_assign_client(request, pk):
    """Asigna un entrenador a un cliente — solo superusuario (única que asigna)."""
    client = get_object_or_404(clients_for_trainer(request.user), pk=pk)   # la superusuaria ve todas, menos registros sin confirmar
    # Solo entrenadores activos (un entrenador desactivado no puede tener clientas)
    trainers = User.objects.filter(role='trainer', is_active=True, erased_at__isnull=True).order_by('first_name')

    if request.method == 'POST':
        trainer_id = request.POST.get('trainer_id') or None
        if trainer_id:
            if not str(trainer_id).isdigit():
                raise Http404
            client.assigned_trainer = get_object_or_404(trainers, pk=int(trainer_id))
        else:
            client.assigned_trainer = None
        client.save()
        messages.success(request, f'Entrenador/a asignado/a a {client.get_full_name()}.')
        return redirect('trainer_client_detail', pk=client.pk)

    return render(request, 'trainer/assign_trainer.html', {
        'client': client,
        'trainers': trainers,
    })
