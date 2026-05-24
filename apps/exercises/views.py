from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from apps.accounts.decorators import trainer_required
from .models import Exercise, ExerciseCategory


def public_exercises(request):
    exercises = Exercise.objects.filter(
        is_public=True, is_active=True
    ).select_related('category')
    categories = ExerciseCategory.objects.all()

    category_id = request.GET.get('category')
    level = request.GET.get('level')

    if category_id:
        exercises = exercises.filter(category__id=category_id)
    if level:
        exercises = exercises.filter(level=level)

    return render(request, 'public/exercises.html', {
        'exercises': exercises,
        'categories': categories,
        'level_choices': Exercise.LEVEL_CHOICES,
        'selected_category': category_id,
        'selected_level': level,
    })


@login_required
def exercise_detail(request, pk):
    exercise = get_object_or_404(Exercise, pk=pk, is_active=True)

    if not exercise.is_public:
        if not request.user.is_trainer:
            try:
                if not request.user.membership.is_valid:
                    return render(request, 'accounts/membership_expired.html')
            except Exception:
                return render(request, 'accounts/no_membership.html')

    return render(request, 'exercises/detail.html', {'exercise': exercise})


# ─── Trainer CRUD ─────────────────────────────────────────────────────────────

@trainer_required
def trainer_exercise_list(request):
    exercises = Exercise.objects.select_related('category').order_by('name')
    categories = ExerciseCategory.objects.all()
    selected_category = request.GET.get('category')
    if selected_category:
        exercises = exercises.filter(category__id=selected_category)
    return render(request, 'trainer/exercise_list.html', {
        'exercises': exercises,
        'categories': categories,
        'selected_category': selected_category,
    })


@trainer_required
def trainer_exercise_create(request):
    categories = ExerciseCategory.objects.all()
    if request.method == 'POST':
        exercise = Exercise(
            name=request.POST['name'],
            category_id=request.POST.get('category') or None,
            level=request.POST['level'],
            description=request.POST['description'],
            muscles=request.POST['muscles'],
            recommendations=request.POST.get('recommendations', ''),
            video_url=request.POST.get('video_url', '').strip(),
            is_public=request.POST.get('is_public') == 'on',
            is_active=True,
        )
        if 'video_file' in request.FILES:
            exercise.video_file = request.FILES['video_file']
        if 'image' in request.FILES:
            exercise.image = request.FILES['image']
        exercise.save()
        messages.success(request, f'Ejercicio "{exercise.name}" creado correctamente.')
        return redirect('trainer_exercise_list')
    return render(request, 'trainer/exercise_form.html', {
        'categories': categories,
        'level_choices': Exercise.LEVEL_CHOICES,
    })


@trainer_required
def trainer_exercise_edit(request, pk):
    exercise = get_object_or_404(Exercise, pk=pk)
    categories = ExerciseCategory.objects.all()
    if request.method == 'POST':
        exercise.name = request.POST['name']
        exercise.category_id = request.POST.get('category') or None
        exercise.level = request.POST['level']
        exercise.description = request.POST['description']
        exercise.muscles = request.POST['muscles']
        exercise.recommendations = request.POST.get('recommendations', '')
        exercise.video_url = request.POST.get('video_url', '').strip()
        exercise.is_public = request.POST.get('is_public') == 'on'
        if 'video_file' in request.FILES:
            exercise.video_file = request.FILES['video_file']
        if 'image' in request.FILES:
            exercise.image = request.FILES['image']
        exercise.save()
        messages.success(request, f'Ejercicio "{exercise.name}" actualizado.')
        return redirect('trainer_exercise_list')
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
