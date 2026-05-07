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
