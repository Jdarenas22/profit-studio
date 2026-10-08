from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from apps.accounts.decorators import trainer_required
from apps.accounts.form_utils import errors_dict, flash_errors
from apps.accounts.permissions import (
    clients_for_trainer, ensure_client_access, get_client_for_trainer,
)
from .forms import BodyMeasurementForm, DixonTestForm, InitialAssessmentForm
from .models import InitialAssessment, DixonTest, BodyMeasurement

# Campos cuyo error ya pinta la plantilla junto al input (`errors.<campo>`, verificado en
# assessment_create.html, assessment_detail.html y body_measurement_add.html). Lo que no
# esté aquí (p. ej. errores generales `__all__`) se avisa además con `messages`.
ASSESSMENT_RENDERED = ('age', 'sex', 'weight', 'height', 'goal', 'physical_restrictions',
                       'observations', 'p0', 'p1', 'p2', 'dixon_observations')
DIXON_RENDERED = ('p0', 'p1', 'p2', 'dixon_observations')
MEASUREMENT_RENDERED = ('weight', 'height', 'waist_cm', 'notes')


@login_required
def assessment_list(request):
    if not request.user.is_trainer:
        return render(request, 'accounts/forbidden.html', status=403)
    assessments = request.user.conducted_assessments.filter(
        user__in=clients_for_trainer(request.user)
    ).select_related('user', 'dixon_test').order_by('-date')
    return render(request, 'assessments/list.html', {'assessments': assessments})


@trainer_required
def trainer_assessment_create(request, client_pk):
    client = get_client_for_trainer(request, client_pk)

    if request.method == 'POST':
        form = InitialAssessmentForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            with transaction.atomic():
                assessment = InitialAssessment.objects.create(
                    user=client,
                    trainer=request.user,
                    age=data['age'],
                    sex=data['sex'],
                    weight=data['weight'],
                    height=data['height'],
                    goal=data['goal'],
                    physical_restrictions=data['physical_restrictions'],
                    observations=data['observations'],
                )
                # Test de Ruffier-Dickson: solo si llegaron los tres pulsos (el formulario ya
                # rechazó el caso "algunos sí, otros no")
                if form.has_dixon:
                    DixonTest.objects.create(
                        assessment=assessment,
                        p0=data['p0'], p1=data['p1'], p2=data['p2'],
                        observations=data['dixon_observations'],
                    )

            msg = f'Valoración de {client.get_full_name() or client.username} creada. IMC: {assessment.imc}.'
            messages.success(request, msg)
            return redirect('trainer_assessment_detail', pk=assessment.pk)

        errors = errors_dict(form)
        flash_errors(request, form, errors, skip=ASSESSMENT_RENDERED)
        return render(request, 'trainer/assessment_create.html', {
            'client': client,
            'sex_choices': InitialAssessment.SEX_CHOICES,
            'errors': errors,
            'form': request.POST,
        })

    return render(request, 'trainer/assessment_create.html', {
        'client': client,
        'sex_choices': InitialAssessment.SEX_CHOICES,
    })


@trainer_required
def trainer_assessment_detail(request, pk):
    assessment = get_object_or_404(InitialAssessment.objects.select_related('user'), pk=pk)
    ensure_client_access(request, assessment.user)
    try:
        dixon = assessment.dixon_test
    except ObjectDoesNotExist:
        dixon = None

    if request.method == 'POST':
        form = DixonTestForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            obs = data['dixon_observations']
            if dixon:
                dixon.p0, dixon.p1, dixon.p2, dixon.observations = data['p0'], data['p1'], data['p2'], obs
                dixon.save()
            else:
                dixon = DixonTest.objects.create(
                    assessment=assessment, p0=data['p0'], p1=data['p1'], p2=data['p2'], observations=obs
                )
            messages.success(request, f'Test de Ruffier-Dickson guardado. IRD: {dixon.index_value} — {dixon.classification}.')
            return redirect('trainer_assessment_detail', pk=pk)

        errors = errors_dict(form)
        flash_errors(request, form, errors, skip=DIXON_RENDERED)
        return render(request, 'trainer/assessment_detail.html', {
            'assessment': assessment,
            'dixon': dixon,
            'errors': errors,
            'form': request.POST,
        })

    return render(request, 'trainer/assessment_detail.html', {
        'assessment': assessment,
        'dixon': dixon,
    })


# ─── Mediciones corporales ─────────────────────────────────────────────────────

@trainer_required
def trainer_measurement_add(request, client_pk):
    client = get_client_for_trainer(request, client_pk)

    if request.method == 'POST':
        form = BodyMeasurementForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            m = BodyMeasurement.objects.create(
                user=client,
                trainer=request.user,
                weight=data['weight'],
                height=data['height'],        # None si no se indicó
                waist_cm=data['waist_cm'],    # None si no se indicó
                notes=data['notes'],
            )
            messages.success(request, f'Medición registrada. Peso: {m.weight} kg'
                             + (f' — IMC: {m.imc} ({m.imc_classification})' if m.imc else '') + '.')
            return redirect('trainer_client_detail', pk=client_pk)

        errors = errors_dict(form)
        flash_errors(request, form, errors, skip=MEASUREMENT_RENDERED)
        return render(request, 'trainer/body_measurement_add.html', {
            'client': client, 'errors': errors, 'form': request.POST,
            'measurements': client.measurements.all(),
        })

    return render(request, 'trainer/body_measurement_add.html', {
        'client': client, 'errors': {}, 'form': {},
        'measurements': client.measurements.all(),
    })
