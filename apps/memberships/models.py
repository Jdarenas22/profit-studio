from django.db import models
from django.utils import timezone
from datetime import timedelta
from django.conf import settings


class MembershipPlan(models.Model):
    name = models.CharField(max_length=100, verbose_name='Nombre')
    duration_days = models.PositiveIntegerField(verbose_name='Duración (días)')
    reference_price = models.DecimalField(
        max_digits=10, decimal_places=2, verbose_name='Precio referencia (COP)'
    )
    description = models.TextField(blank=True, verbose_name='Descripción')
    is_active = models.BooleanField(default=True, verbose_name='Activo')

    class Meta:
        verbose_name = 'Plan de membresía'
        verbose_name_plural = 'Planes de membresía'
        ordering = ['duration_days']

    def __str__(self):
        return f"{self.name} ({self.duration_days} días)"


class Membership(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='membership',
        verbose_name='Usuario',
    )
    plan = models.ForeignKey(
        MembershipPlan,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='Plan',
    )
    start_date = models.DateField(verbose_name='Fecha de inicio')
    end_date = models.DateField(verbose_name='Fecha de vencimiento')
    is_active = models.BooleanField(default=True, verbose_name='Activa')
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='activated_memberships',
        verbose_name='Activada por',
    )
    notes = models.TextField(blank=True, verbose_name='Notas')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Membresía'
        verbose_name_plural = 'Membresías'

    @property
    def is_valid(self):
        return self.is_active and self.end_date >= timezone.now().date()

    @property
    def days_remaining(self):
        if not self.is_valid:
            return 0
        return (self.end_date - timezone.now().date()).days

    @property
    def status_display(self):
        if not self.is_active:
            return 'Desactivada'
        if self.end_date < timezone.now().date():
            return 'Vencida'
        return 'Activa'

    def activate(self, plan, duration_days, activated_by):
        self.plan = plan
        self.start_date = timezone.now().date()
        self.end_date = self.start_date + timedelta(days=duration_days)
        self.is_active = True
        self.activated_by = activated_by
        self.save()

    def renew(self, duration_days, activated_by):
        today = timezone.now().date()
        # Si ya venció, renueva desde hoy; si aún está vigente, extiende desde fin actual
        base = max(self.end_date, today)
        self.end_date = base + timedelta(days=duration_days)
        self.is_active = True
        self.activated_by = activated_by
        self.save()

    def __str__(self):
        return f"Membresía de {self.user} — {self.status_display}"
