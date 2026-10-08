from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from apps.accounts.decorators import trainer_required
from apps.accounts.form_utils import errors_dict, flash_errors
from .forms import ExerciseFilterForm, ExerciseForm
from .models import Exercise, ExerciseCategory


def public_exercises(request):
    exercises = Exercise.objects.filter(
        is_public=True, is_active=True
    ).select_related('category')
    categories = ExerciseCategory.objects.all()

    # Filtros validados: ?category=abc o un nivel inventado se ignoran (antes daban error 500)
    category, level = ExerciseFilterForm(request.GET).cleaned_filters()

    if category:
        exercises = exercises.filter(category=category)
    if level:
        exercises = exercises.filter(level=level)

    return render(request, 'public/exercises.html', {
        'exercises': exercises,
        'categories': categories,
        'level_choices': Exercise.LEVEL_CHOICES,
        'selected_category': str(category.pk) if category else None,
        'selected_level': level or None,
    })


@login_required
def exercise_detail(request, pk):
    exercise = get_object_or_404(Exercise, pk=pk, is_active=True)

    if not exercise.is_public:
        if not request.user.is_trainer:
            try:
                membership = request.user.membership
            except ObjectDoesNotExist:
                return render(request, 'accounts/no_membership.html')
            if not membership.is_valid:
                return render(request, 'accounts/membership_expired.html')

    return render(request, 'exercises/detail.html', {'exercise': exercise})


# ─── Trainer CRUD ─────────────────────────────────────────────────────────────

@trainer_required
def trainer_exercise_list(request):
    exercises = Exercise.objects.select_related('category').order_by('name')
    categories = ExerciseCategory.objects.all()
    category, _level = ExerciseFilterForm(request.GET).cleaned_filters()
    if category:
        exercises = exercises.filter(category=category)
    return render(request, 'trainer/exercise_list.html', {
        'exercises': exercises,
        'categories': categories,
        'selected_category': str(category.pk) if category else None,
    })


@trainer_required
def trainer_exercise_create(request):
    categories = ExerciseCategory.objects.all()
    if request.method == 'POST':
        form = ExerciseForm(request.POST, request.FILES)
        if form.is_valid():
            exercise = form.save()   # is_active=True por defecto del modelo
            messages.success(request, f'Ejercicio "{exercise.name}" creado correctamente.')
            return redirect('trainer_exercise_list')
        errors = errors_dict(form)
        flash_errors(request, form, errors)   # la plantilla aún no pinta errores por campo
        return render(request, 'trainer/exercise_form.html', {
            'categories': categories,
            'level_choices': Exercise.LEVEL_CHOICES,
            'errors': errors,
            'form': request.POST,
        })
    return render(request, 'trainer/exercise_form.html', {
        'categories': categories,
        'level_choices': Exercise.LEVEL_CHOICES,
    })


@trainer_required
def trainer_exercise_edit(request, pk):
    exercise = get_object_or_404(Exercise, pk=pk)
    categories = ExerciseCategory.objects.all()
    if request.method == 'POST':
        form = ExerciseForm(request.POST, request.FILES, instance=exercise)
        if form.is_valid():
            exercise = form.save()
            messages.success(request, f'Ejercicio "{exercise.name}" actualizado.')
            return redirect('trainer_exercise_list')
        errors = errors_dict(form)
        flash_errors(request, form, errors)
        return render(request, 'trainer/exercise_form.html', {
            # se relee: la instancia del formulario queda con los valores tecleados, aunque sean inválidos
            'exercise': get_object_or_404(Exercise, pk=pk),
            'categories': categories,
            'level_choices': Exercise.LEVEL_CHOICES,
            'errors': errors,
            'form': request.POST,
        })
    return render(request, 'trainer/exercise_form.html', {
        'exercise': exercise,
        'categories': categories,
        'level_choices': Exercise.LEVEL_CHOICES,
    })


@trainer_required
def trainer_exercise_delete(request, pk):
    exercise = get_object_or_404(Exercise, pk=pk)
    if request.method == 'POST':
        name = exercise.name
        exercise.is_active = False
        exercise.save(update_fields=['is_active'])
        messages.success(request, f'Ejercicio "{name}" eliminado.')
        return redirect('trainer_exercise_list')
    return render(request, 'trainer/exercise_confirm_delete.html', {'exercise': exercise})
