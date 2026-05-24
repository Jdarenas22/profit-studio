import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.memberships.models import Membership, MembershipPlan
from apps.accounts.decorators import trainer_required
from apps.accounts.models import User
from .models import Payment, ManualPayment
from .services import compute_integrity_signature, verify_webhook_event

logger = logging.getLogger(__name__)


@login_required
def payment_checkout(request, plan_pk):
    plan = get_object_or_404(MembershipPlan, pk=plan_pk, is_active=True)

    # Reuse an existing pending payment to avoid duplicates on page refresh
    payment = Payment.objects.filter(
        user=request.user,
        plan=plan,
        status=Payment.STATUS_PENDING,
    ).first()

    if not payment:
        payment = Payment.objects.create(
            user=request.user,
            plan=plan,
            amount_cents=int(plan.reference_price * 100),
        )

    integrity_sig = compute_integrity_signature(
        payment.reference,
        payment.amount_cents,
        payment.currency,
        settings.WOMPI_INTEGRITY_SECRET,
    )

    return render(request, 'payments/checkout.html', {
        'plan': plan,
        'payment': payment,
        'integrity_sig': integrity_sig,
        'wompi_public_key': settings.WOMPI_PUBLIC_KEY,
        'redirect_url': request.build_absolute_uri('/payments/return/'),
        'customer_email': request.user.email,
        'customer_name': request.user.get_full_name() or request.user.username,
        'customer_phone': request.user.phone if hasattr(request.user, 'phone') else '',
    })


@login_required
def payment_return(request):
    """
    Wompi redirects here after checkout with ?id=WOMPI_TRANSACTION_ID.
    We look up our Payment record and show the result.
    The webhook may have already fired (status updated) or may still be in-flight.
    """
    transaction_id = request.GET.get('id', '')
    payment = None

    if transaction_id:
        payment = Payment.objects.select_related('plan').filter(
            wompi_transaction_id=transaction_id
        ).first()

    if not payment and transaction_id:
        # Webhook hasn't fired yet — find the user's most recent pending payment
        payment = Payment.objects.select_related('plan').filter(
            user=request.user,
            status=Payment.STATUS_PENDING,
        ).order_by('-created_at').first()

    return render(request, 'payments/return.html', {
        'payment': payment,
        'transaction_id': transaction_id,
    })


@csrf_exempt
@require_POST
def payment_webhook(request):
    """
    Wompi posts signed events here. We verify the signature before acting.
    This endpoint MUST NOT require CSRF — Wompi calls it server-to-server.
    Security is guaranteed by the HMAC-SHA256 signature verification.
    """
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.error("Wompi webhook: invalid JSON body")
        return HttpResponse(status=400)

    if not verify_webhook_event(payload, settings.WOMPI_EVENTS_SECRET):
        logger.warning("Wompi webhook: signature mismatch — rejected")
        return HttpResponse(status=401)

    event = payload.get('event', '')
    if event != 'transaction.updated':
        return HttpResponse(status=200)

    tx = payload.get('data', {}).get('transaction', {})
    reference = tx.get('reference', '')
    wompi_status = tx.get('status', '')
    wompi_id = tx.get('id', '')
    method_type = tx.get('payment_method_type', '')

    try:
        payment = Payment.objects.select_related('user', 'plan').get(reference=reference)
    except Payment.DoesNotExist:
        logger.warning(f"Wompi webhook: unknown reference {reference!r}")
        return HttpResponse(status=200)

    # Idempotency: ignore already-processed approvals
    if payment.status == Payment.STATUS_APPROVED:
        return HttpResponse(status=200)

    payment.wompi_transaction_id = wompi_id
    payment.payment_method_type = method_type
    payment.raw_webhook = payload

    if wompi_status == 'APPROVED':
        payment.status = Payment.STATUS_APPROVED
        payment.save()
        _activate_membership(payment)
        logger.info(f"Payment APPROVED: {reference} | user={payment.user_id} | method={method_type}")
    elif wompi_status == 'DECLINED':
        payment.status = Payment.STATUS_DECLINED
        payment.save()
        logger.info(f"Payment DECLINED: {reference}")
    elif wompi_status == 'VOIDED':
        payment.status = Payment.STATUS_VOIDED
        payment.save()
    else:
        payment.save()

    return HttpResponse(status=200)


def _activate_membership(payment):
    """Activate or renew a membership after a confirmed Wompi payment."""
    if not payment.user or not payment.plan:
        logger.error(f"Cannot activate membership: payment {payment.reference} has no user or plan")
        return
    try:
        today = timezone.now().date()
        membership, created = Membership.objects.get_or_create(
            user=payment.user,
            defaults={
                'plan': payment.plan,
                'start_date': today,
                'end_date': today,
                'is_active': False,
                'activated_by': None,
            },
        )
        if created:
            membership.activate(payment.plan, payment.plan.duration_days, activated_by=None)
        else:
            # Renew/upgrade existing membership
            membership.plan = payment.plan
            membership.save(update_fields=['plan'])
            membership.renew(payment.plan.duration_days, activated_by=None)
        logger.info(f"Membership {'created' if created else 'renewed'} for user {payment.user_id}")
    except Exception as exc:
        logger.error(f"Membership activation failed for payment {payment.reference}: {exc}", exc_info=True)


# ─── Panel de pagos manuales (entrenadora) ─────────────────────────────────────

@trainer_required
def trainer_manual_payment_list(request):
    client_pk = request.GET.get('client')
    payments = ManualPayment.objects.select_related('user', 'trainer', 'plan').order_by('-payment_date')
    client = None
    if client_pk:
        client = get_object_or_404(User, pk=client_pk, role='member')
        payments = payments.filter(user=client)
    total = sum(p.amount for p in payments)
    return render(request, 'trainer/manual_payment_list.html', {
        'payments': payments,
        'client': client,
        'total': total,
    })


@trainer_required
def trainer_manual_payment_add(request, client_pk):
    client = get_object_or_404(User, pk=client_pk, role='member')
    plans = MembershipPlan.objects.filter(is_active=True).order_by('duration_days')
    errors = {}

    if request.method == 'POST':
        amount_raw   = request.POST.get('amount', '').strip()
        method       = request.POST.get('method', 'cash')
        payment_date = request.POST.get('payment_date', '').strip()
        plan_id      = request.POST.get('plan') or None
        notes        = request.POST.get('notes', '').strip()

        if not amount_raw:
            errors['amount'] = 'El monto es obligatorio.'
        if not payment_date:
            errors['payment_date'] = 'La fecha es obligatoria.'

        if not errors:
            try:
                amount = int(amount_raw.replace('.', '').replace(',', ''))
            except ValueError:
                errors['amount'] = 'Monto inválido.'

        if not errors:
            mp = ManualPayment(
                user=client,
                trainer=request.user,
                amount=amount,
                method=method,
                payment_date=payment_date,
                notes=notes,
            )
            if plan_id:
                try:
                    mp.plan = MembershipPlan.objects.get(pk=plan_id)
                except MembershipPlan.DoesNotExist:
                    pass
            if 'receipt' in request.FILES:
                mp.receipt = request.FILES['receipt']
            mp.save()
            messages.success(request, f'Pago de ${amount:,} registrado para {client.get_full_name() or client.username}.')
            return redirect('trainer_client_detail', pk=client_pk)

        return render(request, 'trainer/manual_payment_add.html', {
            'client': client, 'plans': plans, 'errors': errors, 'form': request.POST,
        })

    return render(request, 'trainer/manual_payment_add.html', {
        'client': client, 'plans': plans, 'errors': {}, 'form': {},
    })


# ─── Historial de pagos (cliente) ──────────────────────────────────────────────

@login_required
def member_payment_history(request):
    payments = request.user.manual_payments.select_related('plan', 'trainer').order_by('-payment_date')
    return render(request, 'accounts/payment_history.html', {'payments': payments})
