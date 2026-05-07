from django.db import models
from django.conf import settings

# ─── Constantes centralizadas — fácil de modificar ────────────────────────────
IMC_RANGES = [
    (0,    18.5, 'Bajo peso'),
    (18.5, 25.0, 'Peso normal'),
    (25.0, 30.0, 'Sobrepeso'),
    (30.0, float('inf'), 'Obesidad'),
]

DIXON_RANGES = [
    (float('-inf'), 0.05, 'Excelente'),
    (0.05, 5.05,  'Muy buena'),
    (5.05, 10.05, 'Buena'),
    (10.05, 15.05, 'Regular'),
    (15.05, float('inf'), 'Mala'),
]


def classify_by_ranges(value, ranges):
    for min_val, max_val, label in ranges:
        if min_val <= value < max_val:
            return label
    return 'No clasificado'


def calculate_imc(weight_kg, height_m):
    return float(weight_kg) / (float(height_m) ** 2)


def calculate_dixon_index(p0, p1, p2):
    """IRD = ((P0 + P1 + P2) - 200) / 10"""
    return ((p0 + p1 + p2) - 200) / 10


class InitialAssessment(models.Model):
    SEX_CHOICES = [
        ('M', 'Masculino'),
        ('F', 'Femenino'),
        ('O', 'Otro'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='assessments',
        verbose_name='Cliente',
    )
    trainer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='conducted_assessments',
        verbose_name='Entrenadora',
    )
    date = models.DateField(auto_now_add=True, verbose_name='Fecha')
    age = models.PositiveIntegerField(verbose_name='Edad')
    sex = models.CharField(max_length=1, choices=SEX_CHOICES, verbose_name='Sexo')
    weight = models.DecimalField(max_digits=5, decimal_places=2, verbose_name='Peso (kg)')
    height = models.DecimalField(max_digits=4, decimal_places=2, verbose_name='Estatura (m)')
    goal = models.TextField(verbose_name='Objetivo')
    physical_restrictions = models.TextField(blank=True, verbose_name='Restricciones físicas')
    observations = models.TextField(blank=True, verbose_name='Observaciones')

    # Calculados automáticamente
    imc = models.DecimalField(
        max_digits=5, decimal_places=2,
        null=True, blank=True, verbose_name='IMC'
    )
    imc_classification = models.CharField(
        max_length=50, blank=True, verbose_name='Clasificación IMC'
    )

    class Meta:
        verbose_name = 'Valoración inicial'
        verbose_name_plural = 'Valoraciones iniciales'
        ordering = ['-date']

    def save(self, *args, **kwargs):
        if self.weight and self.height:
            imc_val = calculate_imc(self.weight, self.height)
            self.imc = round(imc_val, 2)
            self.imc_classification = classify_by_ranges(imc_val, IMC_RANGES)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Valoración de {self.user} — {self.date}"


class DixonTest(models.Model):
    assessment = models.OneToOneField(
        InitialAssessment,
        on_delete=models.CASCADE,
        related_name='dixon_test',
        verbose_name='Valoración',
    )
    p0 = models.PositiveIntegerField(verbose_name='Pulso inicial en reposo (P0)')
    p1 = models.PositiveIntegerField(verbose_name='Pulso post-actividad física (P1)')
    p2 = models.PositiveIntegerField(verbose_name='Pulso 1 min después (P2)')

    # Calculados automáticamente
    index_value = models.DecimalField(
        max_digits=6, decimal_places=2,
        null=True, blank=True, verbose_name='IRD'
    )
    classification = models.CharField(
        max_length=100, blank=True, verbose_name='Clasificación'
    )
    observations = models.TextField(blank=True, verbose_name='Observaciones')

    class Meta:
        verbose_name = 'Test de Dickson'
        verbose_name_plural = 'Tests de Dickson'

    def save(self, *args, **kwargs):
        if self.p0 is not None and self.p1 is not None and self.p2 is not None:
            idx = calculate_dixon_index(self.p0, self.p1, self.p2)
            self.index_value = round(idx, 2)
            self.classification = classify_by_ranges(idx, DIXON_RANGES)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Test Dixon — {self.assessment.user} — Índice: {self.index_value}"
