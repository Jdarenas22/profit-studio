from django.shortcuts import render, redirect
from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from apps.accounts.decorators import trainer_required
from apps.accounts.form_utils import errors_dict, flash_errors
from apps.accounts.permissions import get_client_for_trainer
from .forms import ACTIVATE, DEACTIVATE, MembershipManageForm
from .models import MembershipPlan, Membership

# Campos cuyo error ya pinta membership_manage.html junto al input (`errors.<campo>`).
MEMBERSHIP_RENDERED = ('plan_id', 'duration_days', 'notes', 'action')


def plans_view(request):
    plans = MembershipPlan.objects.filter(is_active=True)
    return render(request, 'public/plans.html', {'plans': plans})


@trainer_required
def trainer_membership_manage(request, client_pk):
    client = get_client_for_trainer(request, client_pk)
    plans = MembershipPlan.objects.filter(is_active=True)

    try:
        membership = client.membership
    except ObjectDoesNotExist:
        membership = None

    if request.method == 'POST':
        form = MembershipManageForm(request.POST, membership=membership)
        if not form.is_valid():
            errors = errors_dict(form)
            flash_errors(request, form, errors, skip=MEMBERSHIP_RENDERED)
            return render(request, 'trainer/membership_manage.html', {
                'client': client,
                'membership': membership,
                'plans': plans,
                'errors': errors,
                'form': request.POST,
            })

        data = form.cleaned_data
        action = data['action']
        notes = data['notes']

        if action in (ACTIVATE, 'renew'):
            plan = data['plan_id']
            duration = data['duration_days']

            if membership is None:
                membership = Membership(user=client)
                membership.activate(plan, duration, request.user)
            elif action == ACTIVATE:
                membership.activate(plan, duration, request.user)
            else:
                membership.renew(duration, request.user)

            if notes:
                membership.notes = notes
                membership.save(update_fields=['notes'])

            label = 'activada' if action == ACTIVATE else 'renovada'
            messages.success(request, f'Membresía {label} correctamente. Vence el {membership.end_date.strftime("%d/%m/%Y")}.')

        elif action == DEACTIVATE:
            if membership:
                membership.is_active = False
                membership.save(update_fields=['is_active'])
                messages.success(request, 'Membresía desactivada.')
            else:
                messages.error(request, 'El cliente no tiene una membresía para desactivar.')

        return redirect('trainer_client_detail', pk=client_pk)

    return render(request, 'trainer/membership_manage.html', {
        'client': client,
        'membership': membership,
        'plans': plans,
    })
