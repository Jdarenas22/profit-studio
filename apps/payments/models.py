import uuid
from django.conf import settings
from django.db import models


def _gen_reference():
    return f"PROFIT-{uuid.uuid4().hex[:12].upper()}"


class Payment(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_DECLINED = 'declined'
    STATUS_VOIDED = 'voided'
    STATUS_ERROR = 'error'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pendiente'),
        (STATUS_APPROVED, 'Aprobado'),
        (STATUS_DECLINED, 'Rechazado'),
        (STATUS_VOIDED, 'Anulado'),
        (STATUS_ERROR, 'Error'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='payments',
        verbose_name='Usuario',
    )
    plan = models.ForeignKey(
        'memberships.MembershipPlan',
        on_delete=models.SET_NULL,
        null=True,
        related_name='payments',
        verbose_name='Plan',
    )
    reference = models.CharField(
        max_length=64, unique=True, db_index=True,
        default=_gen_reference,
        verbose_name='Referencia interna',
    )
    amount_cents = models.PositiveIntegerField(verbose_name='Monto en centavos (COP)')
    currency = models.CharField(max_length=3, default='COP')
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES,
        default=STATUS_PENDING, verbose_name='Estado',
    )
    wompi_transaction_id = models.CharField(max_length=128, blank=True, verbose_name='ID transacción Wompi')
    payment_method_type = models.CharField(max_length=50, blank=True, verbose_name='Método de pago')
    raw_webhook = models.JSONField(null=True, blank=True, verbose_name='Payload webhook')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Pago'
        verbose_name_plural = 'Pagos'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.reference} — {self.get_status_display()}"

    @property
    def amount_cop(self):
        return self.amount_cents / 100

    METHOD_LABELS = {
        'CARD': 'Tarjeta',
        'PSE': 'PSE',
        'NEQUI': 'Nequi',
        'BANCOLOMBIA_TRANSFER': 'Botón Bancolombia',
        'DAVIPLATA': 'Daviplata',
        'EFECTY': 'Efecty',
        'QR_CODE': 'QR Bancolombia',
    }

    @property
    def method_display(self):
        return self.METHOD_LABELS.get(self.payment_method_type, self.payment_method_type or '—')


class ManualPayment(models.Model):
    """Pago registrado manualmente por la entrenadora (efectivo, transferencia, etc.)."""
    METHOD_CASH      = 'cash'
    METHOD_TRANSFER  = 'transfer'
    METHOD_NEQUI     = 'nequi'
    METHOD_DAVIPLATA = 'daviplata'
    METHOD_OTHER     = 'other'
    METHOD_CHOICES = [
        (METHOD_CASH,      'Efectivo'),
        (METHOD_TRANSFER,  'Transferencia'),
        (METHOD_NEQUI,     'Nequi'),
        (METHOD_DAVIPLATA, 'Daviplata'),
        (METHOD_OTHER,     'Otro'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='manual_payments',
        verbose_name='Cliente',
    )
    trainer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='registered_payments',
        verbose_name='Registrado por',
    )
    plan = models.ForeignKey(
        'memberships.MembershipPlan',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='manual_payments',
        verbose_name='Plan',
    )
    amount = models.PositiveIntegerField(verbose_name='Monto (COP)')
    method = models.CharField(
        max_length=20, choices=METHOD_CHOICES,
        default=METHOD_CASH, verbose_name='Método de pago',
    )
    payment_date = models.DateField(verbose_name='Fecha de pago')
    receipt = models.ImageField(
        upload_to='receipts/', blank=True, null=True,
        verbose_name='Comprobante (foto)',
    )
    notes = models.TextField(blank=True, verbose_name='Notas')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Pago manual'
        verbose_name_plural = 'Pagos manuales'
        ordering = ['-payment_date', '-created_at']

    def __str__(self):
        name = self.user.get_full_name() or self.user.username
        return f"{name} — ${self.amount:,} — {self.payment_date}"

    @property
    def method_label(self):
        return dict(self.METHOD_CHOICES).get(self.method, self.method)
