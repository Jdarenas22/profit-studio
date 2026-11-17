"""Datos de los avisos por WhatsApp (contrato, sección 5).

Solo ids, códigos y fechas: ningún modelo guarda el texto de un mensaje enviado o recibido, ni el
cuerpo de un webhook, ni datos de salud. El único dato personal es el teléfono (`WhatsAppContact`).
Ninguno se registra en el admin de Django (el admin se salta la regla de asignación).
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

# ─── Tipos de aviso ───────────────────────────────────────────────────────────

KIND_OPTIN_CONFIRM = 'optin_confirm'
KIND_MEMBERSHIP_EXPIRING = 'membership_expiring'
KIND_MEMBERSHIP_EXPIRED = 'membership_expired'
KIND_PLAN_APPROVED = 'plan_approved'
KIND_TRAINER_REVIEW = 'trainer_review'
KIND_PAYMENT_APPROVED = 'payment_approved'
# Mensajes de servicio (texto libre, solo como respuesta dentro de la ventana de 24 h)
KIND_OPTOUT_ACK = 'optout_ack'
KIND_CONFIRM_ACK = 'confirm_ack'
KIND_NOT_ME_ACK = 'not_me_ack'
KIND_AUTOREPLY = 'autoreply'

KIND_CHOICES = [
    (KIND_OPTIN_CONFIRM, 'Confirmación del número'),
    (KIND_MEMBERSHIP_EXPIRING, 'Membresía por vencer'),
    (KIND_MEMBERSHIP_EXPIRED, 'Membresía vencida'),
    (KIND_PLAN_APPROVED, 'Plan aprobado'),
    (KIND_TRAINER_REVIEW, 'Pendientes por revisar'),
    (KIND_PAYMENT_APPROVED, 'Pago recibido'),
    (KIND_OPTOUT_ACK, 'Confirmación de baja'),
    (KIND_CONFIRM_ACK, 'Gracias por confirmar'),
    (KIND_NOT_ME_ACK, 'Número equivocado'),
    (KIND_AUTOREPLY, 'Respuesta automática'),
]
SERVICE_KINDS = (KIND_OPTOUT_ACK, KIND_CONFIRM_ACK, KIND_NOT_ME_ACK, KIND_AUTOREPLY)
TEMPLATE_KINDS = tuple(code for code, _ in KIND_CHOICES if code not in SERVICE_KINDS)


class WhatsAppContact(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_ACTIVE = 'active'
    STATUS_WRONG_NUMBER = 'wrong_number'
    STATUS_UNDELIVERABLE = 'undeliverable'
    STATUS_OPTED_OUT = 'opted_out'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pendiente de confirmar'),
        (STATUS_ACTIVE, 'Activo'),
        (STATUS_WRONG_NUMBER, 'Número equivocado'),
        (STATUS_UNDELIVERABLE, 'No se pudo entregar'),
        (STATUS_OPTED_OUT, 'Dado de baja'),
    ]
    LIVE_STATUSES = (STATUS_PENDING, STATUS_ACTIVE)

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='whatsapp_contact', verbose_name='Usuaria')
    phone_e164 = models.CharField(max_length=16, verbose_name='Celular (E.164)')
    wa_id = models.CharField(max_length=20, blank=True)
    bsuid = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_PENDING)
    consent = models.ForeignKey('health.ConsentRecord', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+')
    confirmation_sent_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    opted_out_at = models.DateTimeField(null=True, blank=True)
    last_inbound_at = models.DateTimeField(null=True, blank=True)
    last_autoreply_at = models.DateTimeField(null=True, blank=True)
    undeliverable_count = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Contacto de WhatsApp'
        verbose_name_plural = 'Contactos de WhatsApp'
        constraints = [
            # Un número no puede estar pendiente o activo en dos cuentas a la vez
            models.UniqueConstraint(fields=['phone_e164'], condition=models.Q(status__in=('pending', 'active')),
                                    name='whatsapp_contact_one_live_phone'),
        ]

    def __str__(self):
        return f'Contacto de WhatsApp #{self.pk} ({self.status})'


class WhatsAppMessage(models.Model):
    """La cola de envíos y el registro de entrega. Nunca guarda el texto ni los parámetros: se
    arman al enviar desde los datos vigentes (`subject_*`)."""
    STATUS_QUEUED = 'queued'
    STATUS_SENDING = 'sending'
    STATUS_ACCEPTED = 'accepted'
    STATUS_SENT = 'sent'
    STATUS_DELIVERED = 'delivered'
    STATUS_READ = 'read'
    STATUS_FAILED = 'failed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_EXPIRED = 'expired'
    STATUS_CHOICES = [
        (STATUS_QUEUED, 'En cola'),
        (STATUS_SENDING, 'Enviando'),
        (STATUS_ACCEPTED, 'Aceptado por WhatsApp'),
        (STATUS_SENT, 'Enviado'),
        (STATUS_DELIVERED, 'Entregado'),
        (STATUS_READ, 'Leído'),
        (STATUS_FAILED, 'No se pudo enviar'),
        (STATUS_CANCELLED, 'Cancelado'),
        (STATUS_EXPIRED, 'Vencido sin enviar'),
    ]
    # Orden para que un webhook atrasado no haga retroceder el estado
    PROGRESS = {STATUS_ACCEPTED: 1, STATUS_SENT: 2, STATUS_DELIVERED: 3, STATUS_READ: 4}

    SUBJECT_MEMBERSHIP = 'membership'
    SUBJECT_PLAN = 'plan'
    SUBJECT_PAYMENT = 'payment'
    SUBJECT_CONTACT = 'contact'
    SUBJECT_NONE = 'none'
    SUBJECT_CHOICES = [(SUBJECT_MEMBERSHIP, 'Membresía'), (SUBJECT_PLAN, 'Plan'), (SUBJECT_PAYMENT, 'Pago'),
                       (SUBJECT_CONTACT, 'Contacto'), (SUBJECT_NONE, 'Ninguno')]

    ref = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name='whatsapp_messages')
    kind = models.CharField(max_length=24, choices=KIND_CHOICES)
    template_name = models.CharField(max_length=80, blank=True)
    subject_type = models.CharField(max_length=12, choices=SUBJECT_CHOICES, default=SUBJECT_NONE)
    subject_id = models.PositiveBigIntegerField(null=True, blank=True)
    # Fecha a la que se refiere el aviso (vencimiento de la membresía): si cambió, el aviso se cancela
    subject_date = models.DateField(null=True, blank=True)
    dedupe_key = models.CharField(max_length=120, unique=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    locked_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    wamid = models.CharField(max_length=128, unique=True, null=True, blank=True)
    error_code = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    accepted_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    pricing_category = models.CharField(max_length=20, blank=True)
    pricing_type = models.CharField(max_length=30, blank=True)

    class Meta:
        verbose_name = 'Aviso por WhatsApp'
        verbose_name_plural = 'Avisos por WhatsApp'
        ordering = ['-created_at', '-pk']
        indexes = [
            models.Index(fields=['status', 'next_attempt_at'], name='whatsapp_msg_due'),
            models.Index(fields=['recipient', 'created_at'], name='whatsapp_msg_recipient'),
        ]

    def __str__(self):
        return f'Aviso #{self.pk} {self.kind} ({self.status})'

    @property
    def is_service(self):
        return self.kind in SERVICE_KINDS


class WhatsAppInbound(models.Model):
    """Respuesta recibida, SIN contenido: solo para no procesarla dos veces y auditar la baja."""
    KIND_CONFIRM = 'confirm'
    KIND_NOT_ME = 'not_me'
    KIND_OPTOUT = 'optout'
    KIND_OTHER = 'other'
    KIND_UNSUPPORTED = 'unsupported'
    KIND_CHOICES = [(KIND_CONFIRM, 'Confirmó'), (KIND_NOT_ME, 'No soy yo'), (KIND_OPTOUT, 'Baja'),
                    (KIND_OTHER, 'Otro mensaje'), (KIND_UNSUPPORTED, 'No soportado')]

    wamid = models.CharField(max_length=128, unique=True)
    contact = models.ForeignKey(WhatsAppContact, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='inbound')
    kind = models.CharField(max_length=12, choices=KIND_CHOICES)
    in_reply_to = models.ForeignKey(WhatsAppMessage, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')
    received_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = 'Respuesta de WhatsApp'
        verbose_name_plural = 'Respuestas de WhatsApp'


class WhatsAppChannelState(models.Model):
    """Una sola fila (pk=1): pausa del canal o de plantillas. En la base y no en caché porque la
    caché local es por proceso y el cron es otro proceso."""
    paused = models.BooleanField(default=False)
    paused_reason = models.CharField(max_length=40, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    paused_templates = models.JSONField(default=list, blank=True)
    last_alert_at = models.DateTimeField(null=True, blank=True)
    last_alert_reason = models.CharField(max_length=60, blank=True)

    class Meta:
        verbose_name = 'Estado del canal de WhatsApp'
        verbose_name_plural = 'Estado del canal de WhatsApp'

    @classmethod
    def get(cls):
        state, _ = cls.objects.get_or_create(pk=1)
        return state
