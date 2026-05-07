import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.memberships.models import Membership, MembershipPlan
from .models import Payment
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
