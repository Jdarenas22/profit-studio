from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
import urllib.parse
from .decorators import trainer_required
from .models import User


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
            return redirect(request.GET.get('next', 'dashboard'))
        else:
            messages.error(request, 'Usuario o contraseña incorrectos.')

    return render(request, 'accounts/login.html')


def logout_view(request):
    logout(request)
    return redirect('home')


@login_required
def dashboard(request):
    if request.user.is_trainer:
        return redirect('trainer_dashboard')

    try:
        membership = request.user.membership
        if not membership.is_valid:
            return render(request, 'accounts/membership_expired.html', {
                'membership': membership,
            })
    except Exception:
        return render(request, 'accounts/no_membership.html')

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

    today = timezone.now().date()
    is_super = request.user.is_superuser

    # Superusuario ve todo; otros entrenadores solo sus clientes asignados
    if is_super:
        my_clients_qs = User.objects.filter(role='member')
    else:
        my_clients_qs = User.objects.filter(role='member', assigned_trainer=request.user)

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
            is_active=True, trainer=request.user
        ).count() if not is_super else Routine.objects.filter(is_active=True).count(),
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


def register_view(request):
    """Auto-registro público: cualquier visitante puede crear su cuenta de miembro."""
    if request.user.is_authenticated:
        return redirect('dashboard')

    from apps.memberships.models import MembershipPlan
    plans = MembershipPlan.objects.filter(is_active=True).order_by('duration_days')
    selected_plan_id = request.GET.get('plan') or request.POST.get('interested_plan')
    goal_choices = User.GOAL_CHOICES

    errors = {}

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip().lower()
        username = request.POST.get('username', '').strip().lower()
        phone = request.POST.get('phone', '').strip()
        gender = request.POST.get('gender', '')
        password1 = request.POST.get('password1', '')
        password2 = request.POST.get('password2', '')
        plan_id = request.POST.get('interested_plan') or None

        if not first_name:
            errors['first_name'] = 'El nombre es obligatorio.'
        if not last_name:
            errors['last_name'] = 'El apellido es obligatorio.'
        if not email:
            errors['email'] = 'El correo es obligatorio.'
        elif User.objects.filter(email__iexact=email).exists():
            errors['email'] = 'Ya existe una cuenta con ese correo.'
        if not username:
            errors['username'] = 'El usuario es obligatorio.'
        elif len(username) < 4:
            errors['username'] = 'Mínimo 4 caracteres.'
        elif not username.replace('_', '').replace('.', '').isalnum():
            errors['username'] = 'Solo letras, números, puntos y guiones bajos.'
        elif User.objects.filter(username__iexact=username).exists():
            errors['username'] = 'Ese nombre de usuario ya está en uso.'
        if len(password1) < 8:
            errors['password1'] = 'La contraseña debe tener mínimo 8 caracteres.'
        if password1 != password2:
            errors['password2'] = 'Las contraseñas no coinciden.'

        training_goal = request.POST.get('training_goal', '')

        if not errors:
            plan = None
            if plan_id:
                try:
                    plan = MembershipPlan.objects.get(pk=plan_id, is_active=True)
                except MembershipPlan.DoesNotExist:
                    pass

            user = User.objects.create_user(
                username=username,
                email=email,
                password=password1,
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                gender=gender if gender in ('F', 'M', 'O') else '',
                role=User.ROLE_MEMBER,
                interested_plan=plan,
                training_goal=training_goal if training_goal in dict(User.GOAL_CHOICES) else '',
            )
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')

            # ── Notificar a la entrenadora por email ──────────────────────────
            try:
                from django.core.mail import send_mail
                from django.conf import settings as _cfg
                superuser_emails = list(
                    User.objects.filter(is_superuser=True).exclude(email='')
                    .values_list('email', flat=True)
                )
                if superuser_emails:
                    plan_txt  = plan.name if plan else 'Sin plan'
                    goal_txt  = dict(User.GOAL_CHOICES).get(training_goal, 'No indicado')
                    send_mail(
                        subject=f'🏋️ Nuevo registro: {user.get_full_name() or user.username}',
                        message=(
                            f'Nueva inscripción en ProFit Studio:\n\n'
                            f'Nombre: {user.get_full_name()}\n'
                            f'Usuario: {user.username}\n'
                            f'Email: {user.email}\n'
                            f'Teléfono: {user.phone or "No indicado"}\n'
                            f'Plan de interés: {plan_txt}\n'
                            f'Objetivo: {goal_txt}\n\n'
                            f'Accede al panel para asignarle entrenadora y membresía.'
                        ),
                        from_email=getattr(_cfg, 'DEFAULT_FROM_EMAIL', 'noreply@profitstudio.com'),
                        recipient_list=superuser_emails,
                        fail_silently=True,
                    )
            except Exception:
                pass

            return redirect('register_success')

        return render(request, 'accounts/register.html', {
            'plans': plans,
            'selected_plan_id': plan_id,
            'goal_choices': goal_choices,
            'errors': errors,
            'form': request.POST,
        })

    return render(request, 'accounts/register.html', {
        'plans': plans,
        'selected_plan_id': selected_plan_id,
        'goal_choices': goal_choices,
        'errors': {},
        'form': {},
    })


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
    errors = {}

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip().lower()
        username = request.POST.get('username', '').strip().lower()
        phone = request.POST.get('phone', '').strip()
        gender = request.POST.get('gender', '')
        password = request.POST.get('password', '')
        plan_id = request.POST.get('activate_plan') or None

        if not first_name:
            errors['first_name'] = 'Requerido.'
        if not last_name:
            errors['last_name'] = 'Requerido.'
        if not email:
            errors['email'] = 'Requerido.'
        elif User.objects.filter(email__iexact=email).exists():
            errors['email'] = 'Ya existe una cuenta con ese correo.'
        if not username:
            errors['username'] = 'Requerido.'
        elif User.objects.filter(username__iexact=username).exists():
            errors['username'] = 'Nombre de usuario ya en uso.'
        if len(password) < 8:
            errors['password'] = 'Mínimo 8 caracteres.'

        if not errors:
            client = User.objects.create_user(
                username=username, email=email, password=password,
                first_name=first_name, last_name=last_name,
                phone=phone, role=User.ROLE_MEMBER,
                gender=gender if gender in ('F', 'M', 'O') else '',
            )
            if plan_id:
                try:
                    plan = MembershipPlan.objects.get(pk=plan_id)
                    membership = Membership(user=client)
                    membership.activate(plan, plan.duration_days, request.user)
                    messages.success(
                        request,
                        f'Cliente {client.get_full_name()} creado y membresía "{plan.name}" activada.'
                    )
                except MembershipPlan.DoesNotExist:
                    messages.success(request, f'Cliente {client.get_full_name()} creado correctamente.')
            else:
                messages.success(request, f'Cliente {client.get_full_name()} creado. Recuerda activar su membresía.')

            return redirect('trainer_client_detail', pk=client.pk)

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
    if is_super:
        clients = User.objects.filter(role='member').select_related('assigned_trainer').order_by('first_name', 'last_name')
        unassigned_count = User.objects.filter(role='member', assigned_trainer__isnull=True).count()
    else:
        clients = User.objects.filter(role='member', assigned_trainer=request.user).order_by('first_name', 'last_name')
        unassigned_count = 0
    return render(request, 'trainer/clients.html', {
        'clients': clients,
        'is_superuser': is_super,
        'unassigned_count': unassigned_count,
    })


@trainer_required
def trainer_client_detail(request, pk):
    client = get_object_or_404(User, pk=pk, role='member')
    try:
        membership = client.membership
    except Exception:
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
    client = get_object_or_404(User, pk=pk, role='member')
    from apps.memberships.models import MembershipPlan
    plans = MembershipPlan.objects.filter(is_active=True).order_by('duration_days')
    errors = {}

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip().lower()
        username = request.POST.get('username', '').strip().lower()
        phone = request.POST.get('phone', '').strip()
        gender = request.POST.get('gender', '')
        plan_id = request.POST.get('interested_plan') or None
        new_password = request.POST.get('new_password', '')

        if not first_name:
            errors['first_name'] = 'Requerido.'
        if not last_name:
            errors['last_name'] = 'Requerido.'
        if not email:
            errors['email'] = 'Requerido.'
        elif User.objects.filter(email__iexact=email).exclude(pk=pk).exists():
            errors['email'] = 'Ya existe una cuenta con ese correo.'
        if not username:
            errors['username'] = 'Requerido.'
        elif User.objects.filter(username__iexact=username).exclude(pk=pk).exists():
            errors['username'] = 'Nombre de usuario ya en uso.'
        if new_password and len(new_password) < 8:
            errors['new_password'] = 'Mínimo 8 caracteres.'

        if not errors:
            client.first_name = first_name
            client.last_name = last_name
            client.email = email
            client.username = username
            client.phone = phone
            client.gender = gender if gender in ('F', 'M', 'O') else ''
            if plan_id:
                try:
                    client.interested_plan = MembershipPlan.objects.get(pk=plan_id, is_active=True)
                except MembershipPlan.DoesNotExist:
                    client.interested_plan = None
            else:
                client.interested_plan = None
            if new_password:
                client.set_password(new_password)
            client.save()
            messages.success(request, f'Datos de {client.get_full_name()} actualizados correctamente.')
            return redirect('trainer_client_detail', pk=client.pk)

        return render(request, 'trainer/client_edit.html', {
            'client': client,
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
    client = get_object_or_404(User, pk=pk, role='member')
    if request.method == 'POST':
        full_name = client.get_full_name() or client.username
        client.delete()
        messages.success(request, f'El cliente {full_name} ha sido eliminado.')
        return redirect('trainer_clients')
    return render(request, 'trainer/client_confirm_delete.html', {'client': client})


@login_required
def member_profile_edit(request):
    user = request.user
    errors = {}

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip().lower()
        phone = request.POST.get('phone', '').strip()
        gender = request.POST.get('gender', '')
        current_password = request.POST.get('current_password', '')
        new_password = request.POST.get('new_password', '')
        confirm_password = request.POST.get('confirm_password', '')

        if not first_name:
            errors['first_name'] = 'El nombre es obligatorio.'
        if not last_name:
            errors['last_name'] = 'El apellido es obligatorio.'
        if not email:
            errors['email'] = 'El correo es obligatorio.'
        elif User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
            errors['email'] = 'Ya existe una cuenta con ese correo.'

        if new_password or current_password:
            if not user.check_password(current_password):
                errors['current_password'] = 'Contraseña actual incorrecta.'
            elif len(new_password) < 8:
                errors['new_password'] = 'La contraseña debe tener mínimo 8 caracteres.'
            elif new_password != confirm_password:
                errors['confirm_password'] = 'Las contraseñas no coinciden.'

        if not errors:
            user.first_name = first_name
            user.last_name = last_name
            user.email = email
            user.phone = phone
            user.gender = gender if gender in ('F', 'M', 'O') else ''
            if 'profile_photo' in request.FILES:
                user.profile_photo = request.FILES['profile_photo']
            if new_password:
                user.set_password(new_password)
                login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            user.save()
            messages.success(request, 'Tu perfil ha sido actualizado correctamente.')
            return redirect('dashboard')

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
        },
    })


# ── Solo Yiseth (superusuario) puede crear nuevos entrenadores ──────────────────
@login_required
def trainer_create_trainer(request):
    """Vista exclusiva del superusuario para crear cuentas de entrenadores."""
    if not request.user.is_superuser:
        messages.error(request, 'No tienes permiso para acceder a esta sección.')
        return redirect('trainer_dashboard')

    errors = {}

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip().lower()
        username = request.POST.get('username', '').strip().lower()
        phone = request.POST.get('phone', '').strip()
        bio = request.POST.get('bio', '').strip()
        password = request.POST.get('password', '')
        password2 = request.POST.get('password2', '')

        if not first_name:
            errors['first_name'] = 'Requerido.'
        if not last_name:
            errors['last_name'] = 'Requerido.'
        if not email:
            errors['email'] = 'Requerido.'
        elif User.objects.filter(email__iexact=email).exists():
            errors['email'] = 'Ya existe una cuenta con ese correo.'
        if not username:
            errors['username'] = 'Requerido.'
        elif User.objects.filter(username__iexact=username).exists():
            errors['username'] = 'Nombre de usuario ya en uso.'
        if len(password) < 8:
            errors['password'] = 'Mínimo 8 caracteres.'
        if password != password2:
            errors['password2'] = 'Las contraseñas no coinciden.'

        if not errors:
            trainer = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                bio=bio,
                role=User.ROLE_TRAINER,
                is_staff=True,
                is_superuser=False,  # Solo Yiseth es superusuaria
            )
            messages.success(
                request,
                f'Entrenador/a {trainer.get_full_name()} creado/a correctamente. '
                f'Usuario: {trainer.username} — Deberá cambiar su contraseña al ingresar.'
            )
            return redirect('trainer_staff_list')

        return render(request, 'trainer/trainer_form.html', {
            'errors': errors,
            'form': request.POST,
        })

    return render(request, 'trainer/trainer_form.html', {'errors': {}, 'form': {}})


@login_required
def trainer_staff_list(request):
    """Lista de entrenadores — solo superusuario."""
    if not request.user.is_superuser:
        return redirect('trainer_dashboard')
    trainers = User.objects.filter(role='trainer').order_by('first_name', 'last_name')
    return render(request, 'trainer/trainer_list.html', {'trainers': trainers})


@login_required
def trainer_assign_client(request, pk):
    """Asigna un entrenador a un cliente — solo superusuario."""
    if not request.user.is_superuser:
        messages.error(request, 'Sin permiso.')
        return redirect('trainer_clients')

    client = get_object_or_404(User, pk=pk, role='member')
    trainers = User.objects.filter(role='trainer').order_by('first_name')

    if request.method == 'POST':
        trainer_id = request.POST.get('trainer_id') or None
        if trainer_id:
            client.assigned_trainer = get_object_or_404(User, pk=trainer_id, role='trainer')
        else:
            client.assigned_trainer = None
        client.save()
        messages.success(request, f'Entrenador/a asignado/a a {client.get_full_name()}.')
        return redirect('trainer_client_detail', pk=client.pk)

    return render(request, 'trainer/assign_trainer.html', {
        'client': client,
        'trainers': trainers,
    })
