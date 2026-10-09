"""Consentimiento para el tratamiento de datos de salud (Ley 1581 de 2012).

- `ConsentTextVersion`: el texto exacto que se le muestra a la clienta, versionado. Lo publica
  solo la superusuaria (admin). Una versión publicada no se edita: se crea la siguiente.
- `ConsentRecord`: quién aceptó qué versión y cuándo, y cuándo la retiró. No se guarda la IP
  (minimización de datos).

La ficha de salud (`HealthProfile`) llega en la fase A2 de docs/contracts/plan-ia.md.
"""
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

PURPOSE_HEALTH_DATA = 'health_data'
PURPOSE_AI = 'ai_processing'
PURPOSE_CHOICES = [
    (PURPOSE_HEALTH_DATA, 'Tratamiento de datos de salud'),
    (PURPOSE_AI, 'Envío sin identificar a la inteligencia artificial'),
]
PURPOSES = tuple(code for code, _ in PURPOSE_CHOICES)

# Marcador pendiente de reemplazar en un texto legal: [RAZÓN SOCIAL], [NIT], [CORREO DE DERECHOS]...
PLACEHOLDER_RE = re.compile(r'\[[^\[\]]+\]')


def find_placeholders(text):
    """Marcadores `[...]` que aún quedan en un texto (lista sin repetidos, en orden)."""
    seen = []
    for match in PLACEHOLDER_RE.findall(text or ''):
        if match not in seen:
            seen.append(match)
    return seen


class ConsentTextVersion(models.Model):
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES, verbose_name='Finalidad')
    version = models.PositiveIntegerField(verbose_name='Versión')
    body = models.TextField(verbose_name='Texto completo')
    is_current = models.BooleanField(default=False, verbose_name='Vigente')
    published_at = models.DateTimeField(default=timezone.now, verbose_name='Publicada')

    class Meta:
        verbose_name = 'Texto de consentimiento'
        verbose_name_plural = 'Textos de consentimiento'
        ordering = ['purpose', '-version']
        constraints = [
            models.UniqueConstraint(fields=['purpose', 'version'], name='health_consenttext_unique_version'),
            # Una sola versión vigente por finalidad
            models.UniqueConstraint(
                fields=['purpose'], condition=models.Q(is_current=True),
                name='health_consenttext_one_current',
            ),
        ]

    def __str__(self):
        return f'{self.get_purpose_display()} — v{self.version}'

    @property
    def placeholders(self):
        return find_placeholders(self.body)

    @property
    def has_placeholders(self):
        return bool(self.placeholders)

    def clean(self):
        # Un texto ya usado en una autorización no se reescribe: se publica uno nuevo.
        if self.pk and ConsentRecord.objects.filter(text_version_id=self.pk).exists():
            original = ConsentTextVersion.objects.filter(pk=self.pk).values_list('body', flat=True).first()
            if original is not None and original != self.body:
                raise ValidationError(
                    'Este texto ya fue aceptado por clientas: no se puede modificar. '
                    'Publica una versión nueva.'
                )

    def save(self, *args, **kwargs):
        self.clean()
        with transaction.atomic():
            if self.is_current:
                # Deja una sola vigente por finalidad (antes de guardar, por la restricción)
                ConsentTextVersion.objects.filter(
                    purpose=self.purpose, is_current=True,
                ).exclude(pk=self.pk).update(is_current=False)
            super().save(*args, **kwargs)


class ConsentRecord(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='consents', verbose_name='Clienta',
    )
    text_version = models.ForeignKey(
        ConsentTextVersion, on_delete=models.PROTECT,
        related_name='records', verbose_name='Texto aceptado',
    )
    granted_at = models.DateTimeField(default=timezone.now, verbose_name='Otorgado')
    revoked_at = models.DateTimeField(null=True, blank=True, verbose_name='Retirado')

    class Meta:
        verbose_name = 'Consentimiento'
        verbose_name_plural = 'Consentimientos'
        ordering = ['-granted_at', '-pk']
        constraints = [
            # Doble clic / doble envío: una sola autorización activa por clienta y versión
            models.UniqueConstraint(
                fields=['user', 'text_version'], condition=models.Q(revoked_at__isnull=True),
                name='health_consent_one_active_per_version',
            ),
        ]

    @property
    def purpose(self):
        return self.text_version.purpose

    @property
    def is_active(self):
        return self.revoked_at is None

    def __str__(self):
        return f'{self.user_id} — {self.text_version}'
