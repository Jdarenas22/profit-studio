from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from apps.accounts.decorators import trainer_required
from .models import Routine, RoutineDay, RoutineExercise


@login_required
def member_routine(request):
    if not request.user.is_trainer:
        try:
            if not request.user.membership.is_valid:
                return render(request, 'accounts/membership_expired.html', {
                    'membership': request.user.membership,
                })
        except Exception:
            return render(request, 'accounts/no_membership.html')

    routines = Routine.objects.filter(
        user=request.user, is_active=True
    ).prefetch_related('days__exercises__exercise')

    return render(request, 'routines/member_routine.html', {'routines': routines})


# ─── Trainer — Routine management ─────────────────────────────────────────────

@trainer_required
def trainer_routine_list(request):
    routines = Routine.objects.select_related('user').filter(is_active=True).order_by('-created_at')
    return render(request, 'trainer/routine_list.html', {'routines': routines})


@trainer_required
def trainer_routine_create(request):
    from apps.accounts.models import User
    clients = User.objects.filter(role='member').order_by('first_name', 'last_name')
    if request.method == 'POST':
        routine = Routine.objects.create(
            name=request.POST['name'],
            user_id=request.POST['client'],
            trainer=request.user,
            notes=request.POST.get('notes', ''),
        )
        messages.success(request, f'Rutina "{routine.name}" creada. Ahora agrégale días y ejercicios.')
        return redirect('trainer_routine_builder', pk=routine.pk)
    return render(request, 'trainer/routine_create.html', {'clients': clients})


@trainer_required
def trainer_routine_builder(request, pk):
    from apps.exercises.models import Exercise, ExerciseCategory
    routine = get_object_or_404(Routine, pk=pk)
    exercises = Exercise.objects.filter(is_active=True).select_related('category').order_by('name')
    categories = ExerciseCategory.objects.all()
    return render(request, 'trainer/routine_builder.html', {
        'routine': routine,
        'exercises': exercises,
        'categories': categories,
    })


@trainer_required
def trainer_add_day(request, pk):
    routine = get_object_or_404(Routine, pk=pk)
    if request.method == 'POST':
        name = request.POST.get('day_name', '').strip()
        if not name:
            return HttpResponse('<p class="text-red-400 text-xs p-2">El nombre del día es obligatorio.</p>')
        order = routine.days.count()
        day = RoutineDay.objects.create(routine=routine, name=name, order=order)
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
    routine = get_object_or_404(Routine, pk=pk)
    day = get_object_or_404(RoutineDay, pk=day_pk, routine=routine)
    if request.method == 'POST':
        day.delete()
        return HttpResponse('')
    return HttpResponse(status=405)


@trainer_required
def trainer_add_exercise_to_day(request, pk, day_pk):
    routine = get_object_or_404(Routine, pk=pk)
    day = get_object_or_404(RoutineDay, pk=day_pk, routine=routine)
    if request.method == 'POST':
        from apps.exercises.models import Exercise
        exercise = get_object_or_404(Exercise, pk=request.POST['exercise'])
        order = day.exercises.count()
        re = RoutineExercise.objects.create(
            day=day,
            exercise=exercise,
            sets=int(request.POST.get('sets', 3)),
            reps=request.POST.get('reps', '10'),
            rest_seconds=int(request.POST.get('rest_seconds', 60)),
            observations=request.POST.get('observations', ''),
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
    routine = get_object_or_404(Routine, pk=pk)
    day = get_object_or_404(RoutineDay, pk=day_pk, routine=routine)
    re = get_object_or_404(RoutineExercise, pk=re_pk, day=day)
    if request.method == 'POST':
        re.delete()
        return HttpResponse('')
    return HttpResponse(status=405)


@trainer_required
def trainer_routine_delete(request, pk):
    routine = get_object_or_404(Routine, pk=pk)
    if request.method == 'POST':
        name = routine.name
        routine.is_active = False
        routine.save(update_fields=['is_active'])
        messages.success(request, f'Rutina "{name}" eliminada.')
        return redirect('trainer_routine_list')
    return HttpResponse(status=405)
