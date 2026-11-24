from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Prefetch, prefetch_related_objects
from django.http import Http404, HttpResponse
from django.utils.html import format_html
from apps.accounts.decorators import trainer_required
from apps.accounts.form_utils import errors_dict, flash_errors
from apps.accounts.permissions import (
    INACTIVE_CLIENT_MESSAGE, ClientInactiveError, clients_for_trainer, ensure_client_active,
    reject_if_inactive,
)
from .forms import RoutineDayForm, RoutineExerciseForm, RoutineForm
from .models import Routine, RoutineDay, RoutineExercise, RoutineDayLog

# Campos cuyo error ya pinta routine_create.html junto al input (`errors.<campo>`).
ROUTINE_RENDERED = ('name', 'notes')


def htmx_form_error(form):
    """Fragmento HTML con el primer error del formulario, para las vistas que responde HTMX.

    Se responde 200 (no 4xx) a propósito: htmx 1.x NO intercambia (swap) las respuestas 4xx/5xx
    y el mensaje no se vería. El mismo estilo que usaba `trainer_add_day`. La cabecera
    `X-Form-Error` permite al front distinguir esta respuesta de un éxito.
    """
    message = next(iter(errors_dict(form).values()))
    response = HttpResponse(format_html('<p class="text-red-400 text-xs p-2" role="alert">{}</p>', message))
    response['X-Form-Error'] = '1'
    return response


def htmx_inactive_client(routine):
    """Fragmento HTMX cuando la clienta está desactivada (su ficha es de solo lectura). Mismo
    formato y misma cabecera que `htmx_form_error`. `None` si está activa."""
    try:
        ensure_client_active(routine.user)
    except ClientInactiveError:
        response = HttpResponse(format_html('<p class="text-red-400 text-xs p-2" role="alert">{}</p>',
                                            INACTIVE_CLIENT_MESSAGE))
        response['X-Form-Error'] = '1'
        return response
    return None


@login_required
def member_routine(request):
    if not request.user.is_trainer:
        try:
            membership = request.user.membership
        except ObjectDoesNotExist:
            return render(request, 'accounts/no_membership.html')
        if not membership.is_valid:
            return render(request, 'accounts/membership_expired.html', {'membership': membership})

    routines = Routine.objects.filter(
        user=request.user, is_active=True
    ).select_related('plan__approved_by').prefetch_related('days__exercises__exercise')

    from apps.plans.texts import DISCLAIMER
    return render(request, 'routines/member_routine.html', {'routines': routines, 'plan_disclaimer': DISCLAIMER})


# ─── Trainer — Routine management ─────────────────────────────────────────────

def get_routine_for_trainer(request, pk):
    """Devuelve la rutina `pk` solo si su cliente es accesible para el usuario; si no, 404."""
    return get_object_or_404(
        Routine.objects.select_related('user'),
        pk=pk,
        user__in=clients_for_trainer(request.user),
    )


@trainer_required
def trainer_routine_list(request):
    routines = Routine.objects.select_related('user').filter(
        is_active=True, user__in=clients_for_trainer(request.user)
    ).order_by('-created_at')
    return render(request, 'trainer/routine_list.html', {'routines': routines})


@trainer_required
def trainer_routine_create(request):
    accessible = clients_for_trainer(request.user)
    # En el selector solo las activas (una desactivada es de solo lectura)
    clients = accessible.filter(is_active=True).order_by('first_name', 'last_name')
    if request.method == 'POST':
        # El destinatario debe ser un cliente (role='member') accesible para este entrenador
        client_id = request.POST.get('client', '')
        if not str(client_id).isdigit():
            raise Http404
        client = get_object_or_404(accessible, pk=int(client_id))
        blocked = reject_if_inactive(request, client)
        if blocked:
            return blocked
        form = RoutineForm(request.POST)
        if form.is_valid():
            routine = Routine.objects.create(
                name=form.cleaned_data['name'],
                user=client,
                trainer=request.user,
                notes=form.cleaned_data['notes'],
            )
            messages.success(request, f'Rutina "{routine.name}" creada. Ahora agrégale días y ejercicios.')
            return redirect('trainer_routine_builder', pk=routine.pk)
        errors = errors_dict(form)
        flash_errors(request, form, errors, skip=ROUTINE_RENDERED)
        return render(request, 'trainer/routine_create.html', {
            'clients': clients, 'errors': errors, 'form': request.POST,
        })
    return render(request, 'trainer/routine_create.html', {'clients': clients})


@trainer_required
def trainer_routine_builder(request, pk):
    from apps.exercises.models import Exercise, ExerciseCategory
    # El permiso lo decide get_routine_for_trainer (404 si la clienta no es accesible); aquí solo se
    # precargan los días y sus ejercicios (con el ejercicio de cada línea) que la plantilla recorre:
    # 2 consultas fijas en vez de 2 por día + 1 por ejercicio. `exists` y `all` de la plantilla usan
    # esa precarga, no consultas nuevas.
    routine = get_routine_for_trainer(request, pk)
    prefetch_related_objects([routine], Prefetch(
        'days',
        queryset=RoutineDay.objects.prefetch_related(
            Prefetch('exercises', queryset=RoutineExercise.objects.select_related('exercise')),
        ),
    ))
    exercises = Exercise.objects.filter(is_active=True).select_related('category').order_by('name')
    categories = ExerciseCategory.objects.all()
    return render(request, 'trainer/routine_builder.html', {
        'routine': routine,
        'exercises': exercises,
        'categories': categories,
    })


@trainer_required
def trainer_add_day(request, pk):
    routine = get_routine_for_trainer(request, pk)
    if request.method == 'POST':
        inactive = htmx_inactive_client(routine)
        if inactive:
            return inactive
        form = RoutineDayForm(request.POST)
        if not form.is_valid():
            return htmx_form_error(form)
        order = routine.days.count()
        day = RoutineDay.objects.create(routine=routine, name=form.cleaned_data['day_name'], order=order)
        from apps.exercises.models import Exercise, ExerciseCategory
        exercises = Exercise.objects.filter(is_active=True).select_related('category').order_by('name')
        categories = ExerciseCategory.objects.all()
        return render(request, 'trainer/partials/routine_day.html', {
            'day': day,
            'routine': routine,
            'exercises': exercises,
            'categories': categories,
        })
    return HttpResponse(status=405)


@trainer_required
def trainer_delete_day(request, pk, day_pk):
    routine = get_routine_for_trainer(request, pk)
    day = get_object_or_404(RoutineDay, pk=day_pk, routine=routine)
    if request.method == 'POST':
        inactive = htmx_inactive_client(routine)
        if inactive:
            return inactive
        day.delete()
        return HttpResponse('')
    return HttpResponse(status=405)


@trainer_required
def trainer_add_exercise_to_day(request, pk, day_pk):
    routine = get_routine_for_trainer(request, pk)
    day = get_object_or_404(RoutineDay, pk=day_pk, routine=routine)
    if request.method == 'POST':
        inactive = htmx_inactive_client(routine)
        if inactive:
            return inactive
        form = RoutineExerciseForm(request.POST)
        if not form.is_valid():
            return htmx_form_error(form)
        data = form.cleaned_data
        order = day.exercises.count()
        re = RoutineExercise.objects.create(
            day=day,
            exercise=data['exercise'],
            sets=data['sets'],
            reps=data['reps'],
            rest_seconds=data['rest_seconds'],
            observations=data['observations'],
            order=order,
        )
        return render(request, 'trainer/partials/routine_exercise.html', {
            're': re,
            'routine': routine,
            'day': day,
        })
    return HttpResponse(status=405)


@trainer_required
def trainer_remove_exercise(request, pk, day_pk, re_pk):
    routine = get_routine_for_trainer(request, pk)
    day = get_object_or_404(RoutineDay, pk=day_pk, routine=routine)
    re = get_object_or_404(RoutineExercise, pk=re_pk, day=day)
    if request.method == 'POST':
        inactive = htmx_inactive_client(routine)
        if inactive:
            return inactive
        re.delete()
        return HttpResponse('')
    return HttpResponse(status=405)


@trainer_required
def trainer_routine_delete(request, pk):
    routine = get_routine_for_trainer(request, pk)
    if request.method == 'POST':
        blocked = reject_if_inactive(request, routine.user)
        if blocked:
            return blocked
        name = routine.name
        routine.is_active = False
        routine.save(update_fields=['is_active'])
        messages.success(request, f'Rutina "{name}" eliminada.')
        return redirect('trainer_routine_list')
    return HttpResponse(status=405)


# ─── Registro de días completados (cliente) ────────────────────────────────────

@login_required
def mark_day_complete(request, day_pk):
    """El cliente marca un día de rutina como completado hoy."""
    if request.method != 'POST':
        return HttpResponse(status=405)

    # Solo días de rutinas activas: un borrador de plan con IA es una rutina inactiva
    day = get_object_or_404(RoutineDay, pk=day_pk, routine__user=request.user, routine__is_active=True)

    from django.utils import timezone
    today = timezone.localdate()

    # Evitar duplicados el mismo día
    if not RoutineDayLog.objects.filter(user=request.user, routine_day=day, completed_at=today).exists():
        RoutineDayLog.objects.create(user=request.user, routine_day=day)
        messages.success(request, f'¡{day.name} completado! Sigue así 💪')
    else:
        messages.info(request, f'Ya registraste {day.name} hoy.')

    return redirect('dashboard')
