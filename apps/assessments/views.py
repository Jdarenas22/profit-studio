from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from apps.accounts.decorators import trainer_required
from apps.accounts.models import User
from .models import InitialAssessment, DixonTest, BodyMeasurement


@login_required
def assessment_list(request):
    if not request.user.is_trainer:
        return render(request, 'accounts/forbidden.html', status=403)
    assessments = request.user.conducted_assessments.select_related('user', 'dixon_test').order_by('-date')
    return render(request, 'assessments/list.html', {'assessments': assessments})


@trainer_required
def trainer_assessment_create(request, client_pk):
    client = get_object_or_404(User, pk=client_pk, role='member')

    if request.method == 'POST':
        assessment = InitialAssessment.objects.create(
            user=client,
            trainer=request.user,
            age=request.POST['age'],
            sex=request.POST['sex'],
            weight=request.POST['weight'],
            height=request.POST['height'],
            goal=request.POST['goal'],
            physical_restrictions=request.POST.get('physical_restrictions', ''),
            observations=request.POST.get('observations', ''),
        )

        # Crear Test de Ruffier-Dickson si se proporcionaron los tres pulsos
        p0_raw = request.POST.get('p0', '').strip()
        p1_raw = request.POST.get('p1', '').strip()
        p2_raw = request.POST.get('p2', '').strip()
        if p0_raw and p1_raw and p2_raw:
            try:
                p0, p1, p2 = int(p0_raw), int(p1_raw), int(p2_raw)
                if p0 >= 0 and p1 >= 0 and p2 >= 0:
                    DixonTest.objects.create(
                        assessment=assessment,
                        p0=p0, p1=p1, p2=p2,
                        observations=request.POST.get('dixon_observations', ''),
                    )
            except (ValueError, TypeError):
                pass

        msg = f'Valoración de {client.get_full_name() or client.username} creada. IMC: {assessment.imc}.'
        messages.success(request, msg)
        return redirect('trainer_assessment_detail', pk=assessment.pk)

    return render(request, 'trainer/assessment_create.html', {
        'client': client,
        'sex_choices': InitialAssessment.SEX_CHOICES,
    })


@trainer_required
def trainer_assessment_detail(request, pk):
    assessment = get_object_or_404(InitialAssessment, pk=pk)
    try:
        dixon = assessment.dixon_test
    except Exception:
        dixon = None

    if request.method == 'POST':
        p0 = int(request.POST['p0'])
        p1 = int(request.POST['p1'])
        p2 = int(request.POST['p2'])
        obs = request.POST.get('dixon_observations', '')

        if dixon:
            dixon.p0, dixon.p1, dixon.p2, dixon.observations = p0, p1, p2, obs
            dixon.save()
        else:
            dixon = DixonTest.objects.create(
                assessment=assessment, p0=p0, p1=p1, p2=p2, observations=obs
            )
        messages.success(request, f'Test de Ruffier-Dickson guardado. IRD: {dixon.index_value} — {dixon.classification}.')
        return redirect('trainer_assessment_detail', pk=pk)

    return render(request, 'trainer/assessment_detail.html', {
        'assessment': assessment,
        'dixon': dixon,
    })


# ─── Mediciones corporales ─────────────────────────────────────────────────────

@trainer_required
def trainer_measurement_add(request, client_pk):
    client = get_object_or_404(User, pk=client_pk, role='member')

    if request.method == 'POST':
        weight_raw = request.POST.get('weight', '').strip()
        height_raw = request.POST.get('height', '').strip()
        waist_raw  = request.POST.get('waist_cm', '').strip()

        errors = {}
        if not weight_raw:
            errors['weight'] = 'El peso es obligatorio.'
        try:
            weight = float(weight_raw.replace(',', '.')) if weight_raw else None
        except ValueError:
            errors['weight'] = 'Peso inválido.'
            weight = None

        if not errors:
            m = BodyMeasurement(
                user=client,
                trainer=request.user,
                weight=weight,
                notes=request.POST.get('notes', ''),
            )
            if height_raw:
                try:
                    m.height = float(height_raw.replace(',', '.'))
                except ValueError:
                    pass
            if waist_raw:
                try:
                    m.waist_cm = float(waist_raw.replace(',', '.'))
                except ValueError:
                    pass
            m.save()
            messages.success(request, f'Medición registrada. Peso: {m.weight} kg'
                             + (f' — IMC: {m.imc} ({m.imc_classification})' if m.imc else '') + '.')
            return redirect('trainer_client_detail', pk=client_pk)

        measurements = client.measurements.all()
        return render(request, 'trainer/body_measurement_add.html', {
            'client': client, 'errors': errors, 'form': request.POST,
            'measurements': measurements,
        })

    measurements = client.measurements.all()
    return render(request, 'trainer/body_measurement_add.html', {
        'client': client, 'errors': {}, 'form': {},
        'measurements': measurements,
    })
