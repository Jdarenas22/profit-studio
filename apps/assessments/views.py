from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from apps.accounts.decorators import member_required, trainer_required
from apps.accounts.form_utils import errors_dict, flash_errors
from apps.accounts.permissions import (
    clients_for_trainer, ensure_client_access, get_client_for_trainer,
)
from apps.health import services as health_services
from apps.health.decorators import health_feature_required
from apps.health.models import PURPOSE_HEALTH_DATA
from . import services
from .forms import BodyMeasurementForm, DixonTestForm, InitialAssessmentForm, MemberMeasurementForm
from .models import InitialAssessment, DixonTest, BodyMeasurement

# Campos cuyo error ya pinta la plantilla junto al input (`errors.<campo>`, verificado en
# assessment_create.html, assessment_detail.html y body_measurement_add.html). Lo que no
# esté aquí (p. ej. errores generales `__all__`) se avisa además con `messages`.
ASSESSMENT_RENDERED = ('age', 'sex', 'weight', 'height', 'goal', 'physical_restrictions',
                       'observations', 'p0', 'p1', 'p2', 'dixon_observations')
DIXON_RENDERED = ('p0', 'p1', 'p2', 'dixon_observations')
# Las plantillas de medición (trainer/body_measurement_add.html y _edit.html, y las de la
# clienta) deben pintar `errors.<campo>` de TODOS estos campos (ver docs/contracts/requests.md).
MEASUREMENT_RENDERED = ('weight', 'height', 'waist_cm', 'hip_cm', 'body_fat_pct', 'notes')


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

@never_cache
@trainer_required
def trainer_measurement_add(request, client_pk):
    client = get_client_for_trainer(request, client_pk)

    if request.method == 'POST':
        form = BodyMeasurementForm(request.POST)
        if form.is_valid():
            m = services.create_trainer_measurement(client, request.user, form.cleaned_data)
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


def _measurement_form_values(m):
    """Valores de una medición como texto, para repoblar el formulario (`form.<campo>`)."""
    def txt(value):
        return '' if value is None else str(value)
    return {
        'weight': txt(m.weight), 'height': txt(m.height), 'waist_cm': txt(m.waist_cm),
        'hip_cm': txt(m.hip_cm), 'body_fat_pct': txt(m.body_fat_pct), 'notes': m.notes,
    }


def _get_measurement_for_trainer(request, pk):
    """La medición `pk` solo si su clienta es accesible para el entrenador; si no, 404."""
    m = get_object_or_404(BodyMeasurement.objects.select_related('user'), pk=pk)
    ensure_client_access(request, m.user)
    return m


@never_cache
@trainer_required
def trainer_measurement_edit(request, pk):
    m = _get_measurement_for_trainer(request, pk)
    client = m.user

    if request.method == 'POST':
        form = BodyMeasurementForm(request.POST)
        if form.is_valid():
            services.update_measurement(m, form.cleaned_data)
            messages.success(request, 'Medición actualizada.')
            return redirect('trainer_client_detail', pk=client.pk)
        errors = errors_dict(form)
        flash_errors(request, form, errors, skip=MEASUREMENT_RENDERED)
        values = request.POST
    else:
        errors, values = {}, _measurement_form_values(m)

    return render(request, 'trainer/body_measurement_edit.html', {
        'client': client, 'measurement': m, 'errors': errors, 'form': values,
    })


@never_cache
@trainer_required
@require_POST
def trainer_measurement_delete(request, pk):
    m = _get_measurement_for_trainer(request, pk)
    client_pk = m.user_id
    m.delete()
    messages.success(request, 'Medición eliminada.')
    return redirect('trainer_client_detail', pk=client_pk)


# ─── Mediciones corporales — la clienta ───────────────────────────────────────
# Acceso: sesión + cliente (un entrenador recibe 403) + membresía vigente + función de salud
# encendida. Siempre se trabaja sobre `request.user`: ninguna ruta recibe el id de una clienta.
# Todas las respuestas llevan Cache-Control: no-store (health_feature_required).

def _consent_redirect():
    return redirect(f"{reverse('member_consent', args=[PURPOSE_HEALTH_DATA])}"
                    f"?next={reverse('member_measurement_add')}")


@health_feature_required
@member_required
def member_measurement_list(request):
    return render(request, 'assessments/member_measurements.html', {
        'measurements': services.member_measurements(request.user),
        'series': services.measurement_series(request.user),
        'needs_consent': not health_services.has_health_consent(request.user),
        'is_minor': health_services.is_minor(request.user),
        'daily_limit_reached': services.member_daily_limit_reached(request.user),
    })


def _add_context(request, form_values, errors):
    known = services.latest_height(request.user)
    return {
        'height_required': known is None,
        'last_height': known,
        'daily_limit_reached': services.member_daily_limit_reached(request.user),
        'errors': errors,
        'form': form_values,
    }


@health_feature_required
@member_required
def member_measurement_add(request):
    if health_services.is_minor(request.user):
        return render(request, 'health/minor_blocked.html', status=403)
    if not health_services.has_health_consent(request.user):
        return _consent_redirect()

    known_height = services.latest_height(request.user)

    if request.method == 'POST':
        if services.member_daily_limit_reached(request.user):
            errors = {'general': f'Ya registraste {services.MEMBER_DAILY_LIMIT} mediciones hoy. '
                                 'Si te equivocaste, borra una de hoy y vuelve a intentarlo.'}
            return render(request, 'assessments/member_measurement_add.html',
                          _add_context(request, request.POST, errors))

        form = MemberMeasurementForm(request.POST, known_height=known_height)
        if form.is_valid():
            m = services.create_member_measurement(request.user, form.cleaned_data, form.effective_height)
            if m.needs_review:
                messages.warning(
                    request,
                    'Tu peso cambió mucho frente a tu última medición. Si fue un error al escribirlo, '
                    'puedes borrar esta medición hoy y registrarla de nuevo; tu entrenador también la revisará.',
                )
            messages.success(request, 'Medición registrada.')
            return redirect('member_measurement_list')

        errors = errors_dict(form)
        flash_errors(request, form, errors, skip=MEASUREMENT_RENDERED)
        return render(request, 'assessments/member_measurement_add.html',
                      _add_context(request, request.POST, errors))

    return render(request, 'assessments/member_measurement_add.html', _add_context(request, {}, {}))


@health_feature_required
@member_required
@require_POST
def member_measurement_delete(request, pk):
    # Solo las propias: la de otra clienta (o inexistente) responde 404 igual
    m = get_object_or_404(BodyMeasurement, pk=pk, user=request.user)
    if not services.can_member_delete(m, request.user):
        messages.error(request, 'Solo puedes borrar las mediciones que registraste tú hoy. '
                                'Si necesitas corregir otra, avísale a tu entrenador.')
        return redirect('member_measurement_list')
    m.delete()
    messages.success(request, 'Medición eliminada.')
    return redirect('member_measurement_list')
