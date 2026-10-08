import logging
import urllib.parse

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.mail import BadHeaderError, send_mail
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .axes_utils import get_client_ip
from .decorators import trainer_required, superuser_required
from .form_utils import errors_dict, flash_errors
from .forms import (
    MemberProfileForm, RegisterForm, TrainerAddClientForm, TrainerClientEditForm,
    TrainerCreateTrainerForm, TrainerEditForm,
)
from .models import User
from .permissions import clients_for_trainer, get_client_for_trainer
from .ratelimit import hit

logger = logging.getLogger(__name__)

# Campos que cada plantilla ya pinta junto al input (`errors.<campo>`): el resto de
# errores se avisa además con `messages` para que nunca fallen en silencio.
REGISTER_RENDERED = ('first_name', 'last_name', 'email', 'username', 'password1', 'password2')
CLIENT_ADD_RENDERED = ('first_name', 'last_name', 'email', 'username', 'password')
CLIENT_EDIT_RENDERED = ('first_name', 'last_name', 'email', 'username', 'new_password')
PROFILE_RENDERED = ('first_name', 'last_name', 'email', 'current_password', 'new_password',
                    'confirm_password')
TRAINER_FORM_RENDERED = ('first_name', 'last_name', 'email', 'username', 'password', 'password2')
TRAINER_EDIT_RENDERED = ('first_name', 'last_name', 'email')

# Límites del registro público (por IP, ventana fija). Se pueden ajustar en settings.
REGISTER_ATTEMPTS_LIMIT = getattr(settings, 'REGISTER_ATTEMPTS_LIMIT', 20)    # envíos de formulario / ventana
REGISTER_SUCCESS_LIMIT = getattr(settings, 'REGISTER_SUCCESS_LIMIT', 5)       # cuentas creadas / ventana
REGISTER_WINDOW_SECONDS = getattr(settings, 'REGISTER_WINDOW_SECONDS', 3600)


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

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
        else:
            messages.error(request, 'Usuario o contraseña incorrectos.')

    return render(request, 'accounts/login.html')


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

    routines = request.user.routines.filter(is_active=True).prefetch_related(
        'days__exercises__exercise'
    )
    return render(request, 'accounts/member_dashboard.html', {
        'membership': membership,
        'routines': routines,
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

    # Superusuaria ve todo; los demás entrenadores solo sus clientes asignados
    my_clients_qs = clients_for_trainer(request.user)

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
            role='member', assigned_trainer__isnull=True
        ).count() if is_super else 0,
    }
    return render(request, 'trainer/dashboard.html', context)


def _notify_new_registration(user, plan):
    """Avisa por correo a la(s) superusuaria(s). Un fallo de correo nunca rompe el registro."""
    try:
        superuser_emails = list(
            User.objects.filter(is_superuser=True).exclude(email='')
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
        'plans': plans,
        'selected_plan_id': selected_plan_id,
        'goal_choices': User.GOAL_CHOICES,
        'errors': errors,
        'form': form,
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


def register_view(request):
    """Auto-registro público: cualquier visitante puede crear su cuenta de miembro.

    Protecciones: validación con `RegisterForm` (incluye `validate_password`), límite de
    frecuencia por IP (envíos y cuentas creadas por hora) y aviso genérico si el correo
    ya existe.

    NO implementado (opción para decidir): verificación de correo. Consistiría en crear la
    cuenta como inactiva, enviar un enlace firmado (django.core.signing, con caducidad) y
    activarla al abrirlo; con eso la respuesta del registro podría ser idéntica exista o no
    el correo, que es la única forma de cerrar del todo la enumeración de cuentas.
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

    form = RegisterForm(request.POST)
    if form.is_valid():
        # Solo las cuentas realmente creadas cuentan para el límite estricto, así un error de
        # tecleo no bloquea a una persona legítima.
        if not hit('register-success', ip, REGISTER_SUCCESS_LIMIT, REGISTER_WINDOW_SECONDS):
            return _register_rate_limited(request, plans, plan_id)
        data = form.cleaned_data
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
                )
        except IntegrityError:   # carrera: otro registro tomó el mismo usuario justo ahora
            form.add_error('username', form.username_taken_message)
        else:
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            _notify_new_registration(user, data['interested_plan'])
            return redirect('register_success')

    errors = errors_dict(form)
    flash_errors(request, form, errors, skip=REGISTER_RENDERED)
    return render(request, 'accounts/register.html', _register_context(plans, plan_id, errors, request.POST))


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
def trainer_add_client(request):
    """La entrenadora crea una cuenta de cliente y opcionalmente activa su membresía."""
    from apps.memberships.models import MembershipPlan, Membership
    plans = MembershipPlan.objects.filter(is_active=True).order_by('duration_days')

    if request.method == 'POST':
        form = TrainerAddClientForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                with transaction.atomic():
                    client = User.objects.create_user(
                        username=data['username'], email=data['email'], password=data['password'],
                        first_name=data['first_name'], last_name=data['last_name'],
                        phone=data['phone'], role=User.ROLE_MEMBER, gender=data['gender'],
                        # Asignación automática: el cliente queda a cargo de quien lo crea
                        assigned_trainer=request.user,
                    )
            except IntegrityError:   # carrera con otra creación del mismo usuario
                form.add_error('username', form.username_taken_message)
            else:
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
                return redirect('trainer_client_detail', pk=client.pk)

        errors = errors_dict(form)
        flash_errors(request, form, errors, skip=CLIENT_ADD_RENDERED)
        return render(request, 'trainer/client_add.html', {
            'plans': plans,
            'errors': errors,
            'form': request.POST,
        })

    return render(request, 'trainer/client_add.html', {
        'plans': plans,
        'errors': {},
        'form': {},
    })


@trainer_required
def trainer_client_list(request):
    is_super = request.user.is_superuser
    clients = clients_for_trainer(request.user).select_related('assigned_trainer').order_by('first_name', 'last_name')
    unassigned_count = clients.filter(assigned_trainer__isnull=True).count() if is_super else 0
    return render(request, 'trainer/clients.html', {
        'clients': clients,
        'is_superuser': is_super,
        'unassigned_count': unassigned_count,
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
    })


@trainer_required
def trainer_client_edit(request, pk):
    client = get_client_for_trainer(request, pk)
    from apps.memberships.models import MembershipPlan
    plans = MembershipPlan.objects.filter(is_active=True).order_by('duration_days')

    if request.method == 'POST':
        form = TrainerClientEditForm(request.POST, client=client)
        if form.is_valid():
            data = form.cleaned_data
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
                return redirect('trainer_client_detail', pk=client.pk)

        errors = errors_dict(form)
        flash_errors(request, form, errors, skip=CLIENT_EDIT_RENDERED)
        return render(request, 'trainer/client_edit.html', {
            'client': get_client_for_trainer(request, pk),   # se relee: sin datos a medio editar
            'plans': plans,
            'errors': errors,
            'form': request.POST,
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


@trainer_required
def trainer_client_delete(request, pk):
    client = get_client_for_trainer(request, pk)
    if request.method == 'POST':
        full_name = client.get_full_name() or client.username
        client.delete()
        messages.success(request, f'El cliente {full_name} ha sido eliminado.')
        return redirect('trainer_clients')
    return render(request, 'trainer/client_confirm_delete.html', {'client': client})


@login_required
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
            'form': request.POST,
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
            'form': request.POST,
        })

    return render(request, 'trainer/trainer_form.html', {'errors': {}, 'form': {}})


@superuser_required
def trainer_staff_list(request):
    """Lista de entrenadores — solo superusuario."""
    trainers = User.objects.filter(role='trainer').order_by('first_name', 'last_name')
    return render(request, 'trainer/trainer_list.html', {'trainers': trainers})


@superuser_required
def trainer_edit_trainer(request, pk):
    """Editar datos de un entrenador — solo superusuario."""
    trainer = get_object_or_404(User, pk=pk, role='trainer')

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
            'trainer_obj': get_object_or_404(User, pk=pk, role='trainer'),
            'errors': errors,
            'form': request.POST,
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
    client = get_object_or_404(User, pk=pk, role='member')
    trainers = User.objects.filter(role='trainer').order_by('first_name')

    if request.method == 'POST':
        trainer_id = request.POST.get('trainer_id') or None
        if trainer_id:
            if not str(trainer_id).isdigit():
                raise Http404
            client.assigned_trainer = get_object_or_404(User, pk=int(trainer_id), role='trainer')
        else:
            client.assigned_trainer = None
        client.save()
        messages.success(request, f'Entrenador/a asignado/a a {client.get_full_name()}.')
        return redirect('trainer_client_detail', pk=client.pk)

    return render(request, 'trainer/assign_trainer.html', {
        'client': client,
        'trainers': trainers,
    })
