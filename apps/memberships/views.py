from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from apps.accounts.decorators import trainer_required
from apps.accounts.models import User
from .models import MembershipPlan, Membership


def plans_view(request):
    plans = MembershipPlan.objects.filter(is_active=True)
    return render(request, 'public/plans.html', {'plans': plans})


@trainer_required
def trainer_membership_manage(request, client_pk):
    client = get_object_or_404(User, pk=client_pk, role='member')
    plans = MembershipPlan.objects.filter(is_active=True)

    try:
        membership = client.membership
    except Exception:
        membership = None

    if request.method == 'POST':
        action = request.POST.get('action')
        plan_id = request.POST.get('plan_id')
        notes = request.POST.get('notes', '')

        if action in ('activate', 'renew'):
            plan = get_object_or_404(MembershipPlan, pk=plan_id)
            duration = int(request.POST.get('duration_days') or plan.duration_days)

            if membership is None:
                membership = Membership(user=client)
                membership.activate(plan, duration, request.user)
            elif action == 'activate':
                membership.activate(plan, duration, request.user)
            else:
                membership.renew(duration, request.user)

            if notes:
                membership.notes = notes
                membership.save(update_fields=['notes'])

            label = 'activada' if action == 'activate' else 'renovada'
            messages.success(request, f'Membresía {label} correctamente. Vence el {membership.end_date.strftime("%d/%m/%Y")}.')

        elif action == 'deactivate' and membership:
            membership.is_active = False
            membership.save(update_fields=['is_active'])
            messages.success(request, 'Membresía desactivada.')

        return redirect('trainer_client_detail', pk=client_pk)

    return render(request, 'trainer/membership_manage.html', {
        'client': client,
        'membership': membership,
        'plans': plans,
    })
