import json
import logging
import mimetypes

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.memberships.models import MembershipPlan
from apps.accounts.decorators import trainer_required
from apps.accounts.permissions import clients_for_trainer, get_client_for_trainer
from .forms import ManualPaymentForm
from .models import Payment, ManualPayment
from .services import (
    WompiConfigError, apply_transaction_event, compute_integrity_signature, verify_webhook_event,
)

logger = logging.getLogger(__name__)

_CHECKOUT_UNAVAILABLE = 'El pago en línea no está disponible por ahora. Contáctanos por WhatsApp.'


@login_required
def payment_checkout(request, plan_pk):
    plan = get_object_or_404(MembershipPlan, pk=plan_pk, is_active=True)

    # Sin llaves de Wompi no se genera un checkout (la firma sería inválida)
    if not settings.WOMPI_INTEGRITY_SECRET or not settings.WOMPI_PUBLIC_KEY:
        logger.error("Wompi checkout: faltan WOMPI_PUBLIC_KEY / WOMPI_INTEGRITY_SECRET en la configuración")
        messages.error(request, _CHECKOUT_UNAVAILABLE)
        return redirect('plans')

    amount_cents = int(plan.reference_price * 100)

    # Reuse an existing pending payment to avoid duplicates on page refresh
    # (solo si el precio del plan no cambió desde que se creó)
    payment = Payment.objects.filter(
        user=request.user,
        plan=plan,
        status=Payment.STATUS_PENDING,
        amount_cents=amount_cents,
    ).first()

    if not payment:
        payment = Payment.objects.create(
            user=request.user,
            plan=plan,
            amount_cents=amount_cents,
        )

    try:
        integrity_sig = compute_integrity_signature(
            payment.reference,
            payment.amount_cents,
            payment.currency,
            settings.WOMPI_INTEGRITY_SECRET,
        )
    except WompiConfigError:
        logger.error("Wompi checkout: WOMPI_INTEGRITY_SECRET vacío")
        messages.error(request, _CHECKOUT_UNAVAILABLE)
        return redirect('plans')

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
        # Solo el dueño del pago puede verlo (evita leer pagos ajenos con ?id=)
        payment = Payment.objects.select_related('plan').filter(
            wompi_transaction_id=transaction_id, user=request.user
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
    Security is guaranteed by the SHA256 checksum verification
    (propiedades firmadas + timestamp + secreto de eventos).

    Códigos de respuesta (Wompi reintenta hasta 3 veces en 24 h mientras la
    respuesta sea distinta de 200):
      200  evento procesado, duplicado, ignorado o con datos inconsistentes que un
           reintento no arreglaría (monto/moneda distintos, referencia desconocida)
      400  cuerpo que no es JSON válido
      401  firma inválida
      503  falta WOMPI_EVENTS_SECRET (reintentar tiene sentido cuando se configure)
      500  falló la activación de la membresía: se revierte todo y Wompi reintenta
    """
    if not settings.WOMPI_EVENTS_SECRET:
        logger.error("Wompi webhook: WOMPI_EVENTS_SECRET no configurado — no se puede verificar")
        return HttpResponse(status=503)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.error("Wompi webhook: invalid JSON body")
        return HttpResponse(status=400)
    if not isinstance(payload, dict):
        logger.error("Wompi webhook: JSON body is not an object")
        return HttpResponse(status=400)

    if not verify_webhook_event(
        payload, settings.WOMPI_EVENTS_SECRET, request.headers.get('X-Event-Checksum'),
    ):
        logger.warning("Wompi webhook: signature mismatch — rejected")
        return HttpResponse(status=401)

    if payload.get('event') != 'transaction.updated':
        return HttpResponse(status=200)

    try:
        outcome = apply_transaction_event(payload)
    except Exception:
        # La transacción ya se revirtió: el pago sigue como estaba. Wompi reintentará.
        logger.exception("Wompi webhook: fallo al aplicar el evento; se pedirá reintento")
        return HttpResponse(status=500)

    logger.info(f"Wompi webhook processed: {outcome}")
    return HttpResponse(status=200)


# ─── Panel de pagos manuales (entrenadora) ─────────────────────────────────────

@trainer_required
def trainer_manual_payment_list(request):
    client_pk = request.GET.get('client')
    # Solo pagos de clientes accesibles (superusuaria: todos; entrenador: los suyos)
    payments = ManualPayment.objects.select_related('user', 'trainer', 'plan').filter(
        user__in=clients_for_trainer(request.user)
    ).order_by('-payment_date')
    client = None
    if client_pk:
        if not client_pk.isdigit():
            raise Http404
        client = get_client_for_trainer(request, int(client_pk))
        payments = payments.filter(user=client)
    total = sum(p.amount for p in payments)
    return render(request, 'trainer/manual_payment_list.html', {
        'payments': payments,
        'client': client,
        'total': total,
    })


# Campos cuyo error manual_payment_add.html ya pinta junto al input (`errors.<campo>`).
# Cualquier otro error (p. ej. uno general del formulario) se avisa con un mensaje flash para
# que nunca falle en silencio; los pintados NO se repiten como flash.
_FIELDS_WITH_INLINE_ERROR = ('amount', 'payment_date', 'method', 'plan', 'receipt', 'notes')
_FIELD_LABELS = {'method': 'Método de pago', 'plan': 'Plan', 'receipt': 'Comprobante', 'notes': 'Notas',
                 'amount': 'Monto', 'payment_date': 'Fecha de pago', '__all__': 'Formulario'}


@trainer_required
def trainer_manual_payment_add(request, client_pk):
    client = get_client_for_trainer(request, client_pk)
    plans = MembershipPlan.objects.filter(is_active=True).order_by('duration_days')

    if request.method == 'POST':
        form = ManualPaymentForm(request.POST, request.FILES)
        if form.is_valid():
            data = form.cleaned_data
            mp = ManualPayment(
                user=client,
                trainer=request.user,
                amount=data['amount'],
                method=data['method'],
                payment_date=data['payment_date'],
                plan=data['plan'],
                notes=data['notes'],
            )
            if data['receipt']:
                mp.receipt = data['receipt']
            mp.save()
            messages.success(
                request,
                f"Pago de ${mp.amount:,} registrado para {client.get_full_name() or client.username}.",
            )
            return redirect('trainer_client_detail', pk=client_pk)

        errors = form.error_messages_by_field()
        for field, text in errors.items():
            if field not in _FIELDS_WITH_INLINE_ERROR:
                messages.error(request, f"{_FIELD_LABELS.get(field, field)}: {text}")
        # `form` conserva el nombre que usa la plantilla (los valores enviados)
        return render(request, 'trainer/manual_payment_add.html', {
            'client': client, 'plans': plans, 'errors': errors, 'form': request.POST,
        })

    return render(request, 'trainer/manual_payment_add.html', {
        'client': client, 'plans': plans, 'errors': {}, 'form': {},
    })


@login_required
@require_GET
def payment_receipt(request, pk):
    """
    Entrega un comprobante solo a quien corresponde: el propio cliente, o el
    entrenador con acceso a ese cliente (la superusuaria, a todos). Otro caso: 404.
    """
    mp = get_object_or_404(ManualPayment, pk=pk)
    user = request.user
    allowed = mp.user_id == user.pk or (
        user.is_trainer and clients_for_trainer(user).filter(pk=mp.user_id).exists()
    )
    if not allowed or not mp.receipt:
        raise Http404

    try:
        handle = mp.receipt.open('rb')
    except OSError:
        raise Http404
    content_type = mimetypes.guess_type(mp.receipt.name)[0] or 'application/octet-stream'
    response = FileResponse(handle, content_type=content_type)
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    response['Content-Disposition'] = 'inline'
    return response


# ─── Historial de pagos (cliente) ──────────────────────────────────────────────

@login_required
def member_payment_history(request):
    payments = request.user.manual_payments.select_related('plan', 'trainer').order_by('-payment_date')
    return render(request, 'accounts/payment_history.html', {'payments': payments})
