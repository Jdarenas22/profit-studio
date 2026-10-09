"""Consentimiento para el tratamiento de datos de salud (Ley 1581 de 2012).

- `ConsentTextVersion`: el texto exacto que se le muestra a la clienta, versionado. Lo publica
  solo la superusuaria (admin). Una versión publicada no se edita: se crea la siguiente.
- `ConsentRecord`: quién aceptó qué versión y cuándo, y cuándo la retiró. No se guarda la IP
  (minimización de datos).

- `HealthProfile` (fase A2): ficha de salud y alimentación, una fila por versión (una sola
  `is_current` por clienta). `HealthAccessLog`: quién abrió/editó/exportó/borró una ficha.
  Ninguno de los dos se registra en el admin (el admin se salta la regla de asignación).
"""
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.plans.rules import flags as rules_flags

from . import choices as ch
from . import dates

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


# ─── Ficha de salud y alimentación (fase A2) ──────────────────────────────────

class HealthProfile(models.Model):
    """Una versión de la ficha de una clienta. Cada guardado crea una fila nueva y deja una sola
    `is_current`. Las listas cerradas se guardan como listas de códigos (validadas en el servidor
    con apps/health/choices.py). Los textos libres son solo para el entrenador: nunca van a la IA.
    No se escribe ningún campo en logs.
    """
    ROLE_MEMBER = 'member'
    ROLE_TRAINER = 'trainer'
    ROLE_CHOICES = [(ROLE_MEMBER, 'Clienta'), (ROLE_TRAINER, 'Entrenador/a')]

    # Control
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='health_profiles', verbose_name='Clienta',
    )
    version = models.PositiveIntegerField(verbose_name='Versión')
    is_current = models.BooleanField(default=True, verbose_name='Vigente')
    created_at = models.DateTimeField(default=timezone.now, verbose_name='Creada')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Creada por',
    )
    created_by_role = models.CharField(max_length=10, choices=ROLE_CHOICES, verbose_name='Rol de quien la creó')
    consent = models.ForeignKey(
        ConsentRecord, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Autorización vigente al guardar',
    )
    confirmed_by_client_at = models.DateTimeField(null=True, blank=True, verbose_name='Confirmada por la clienta')

    # Básicos (la meta vive en User.training_goal; peso y estatura en BodyMeasurement)
    birth_date = models.DateField(verbose_name='Fecha de nacimiento')
    sex_for_calculation = models.CharField(max_length=2, choices=ch.SEX_CHOICES, verbose_name='Sexo para el cálculo')
    target_weight_kg = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True, verbose_name='Peso meta (kg)',
    )

    # Salud
    conditions = models.JSONField(default=list, blank=True, verbose_name='Condiciones')
    condition_controlled = models.BooleanField(default=False, verbose_name='Condiciones controladas')
    medical_clearance = models.BooleanField(default=False, verbose_name='Autorización médica')
    clearance_date = models.DateField(null=True, blank=True, verbose_name='Fecha de la autorización médica')
    medications = models.JSONField(default=list, blank=True, verbose_name='Medicamentos')
    injuries = models.JSONField(default=list, blank=True, verbose_name='Lesiones')
    recent_surgery = models.CharField(max_length=10, choices=ch.SURGERY_CHOICES, verbose_name='Cirugía reciente')
    pregnancy_status = models.CharField(max_length=20, choices=ch.PREGNANCY_CHOICES, verbose_name='Embarazo o lactancia')
    eating_disorder_history = models.CharField(
        max_length=15, choices=ch.EATING_DISORDER_CHOICES, verbose_name='Antecedentes de trastorno alimentario',
    )
    parq = models.JSONField(default=dict, verbose_name='Cuestionario de aptitud (q1..q7)')

    # Texto libre (solo para el entrenador; se muestra siempre escapado)
    other_condition_text = models.CharField(max_length=300, blank=True, verbose_name='Otra condición')
    medications_text = models.CharField(max_length=300, blank=True, verbose_name='Medicamentos (texto)')
    injuries_text = models.CharField(max_length=300, blank=True, verbose_name='Lesiones (texto)')
    disliked_foods_text = models.CharField(max_length=300, blank=True, verbose_name='Alimentos que no le gustan')
    liked_foods_text = models.CharField(max_length=300, blank=True, verbose_name='Alimentos que le gustan')

    # Alimentación
    diet_type = models.CharField(max_length=15, choices=ch.DIET_CHOICES, verbose_name='Tipo de dieta')
    allergies = models.JSONField(default=list, blank=True, verbose_name='Alergias')
    allergy_severity = models.CharField(
        max_length=12, choices=ch.ALLERGY_SEVERITY_CHOICES, blank=True, verbose_name='Gravedad de la alergia',
    )
    intolerances = models.JSONField(default=list, blank=True, verbose_name='Intolerancias')
    meal_slots = models.JSONField(default=list, verbose_name='Comidas del día')
    meal_times = models.JSONField(default=dict, blank=True, verbose_name='Horarios de las comidas')
    cooking_access = models.CharField(max_length=5, choices=ch.COOKING_CHOICES, verbose_name='Posibilidad de cocinar')
    budget_level = models.CharField(max_length=6, choices=ch.BUDGET_CHOICES, verbose_name='Presupuesto')
    eats_out_per_week = models.PositiveSmallIntegerField(default=0, verbose_name='Comidas fuera por semana')

    # Entrenamiento
    training_days_per_week = models.PositiveSmallIntegerField(verbose_name='Días de entrenamiento por semana')
    session_minutes = models.PositiveSmallIntegerField(verbose_name='Minutos por sesión')
    training_place = models.CharField(max_length=4, choices=ch.TRAINING_PLACE_CHOICES, verbose_name='Lugar de entrenamiento')
    equipment = models.JSONField(default=list, blank=True, verbose_name='Equipo disponible')
    experience_level = models.CharField(max_length=12, choices=ch.EXPERIENCE_CHOICES, verbose_name='Experiencia')
    activity_level = models.CharField(max_length=9, choices=ch.ACTIVITY_CHOICES, verbose_name='Nivel de actividad')
    sleep_hours = models.DecimalField(
        max_digits=3, decimal_places=1, null=True, blank=True, verbose_name='Horas de sueño',
    )

    class Meta:
        verbose_name = 'Ficha de salud'
        verbose_name_plural = 'Fichas de salud'
        ordering = ['-version']
        constraints = [
            models.UniqueConstraint(fields=['user', 'version'], name='health_profile_unique_version'),
            # Una sola ficha vigente por clienta (la integridad no depende solo del código)
            models.UniqueConstraint(
                fields=['user'], condition=models.Q(is_current=True),
                name='health_profile_one_current',
            ),
        ]

    def __str__(self):
        return f'Ficha v{self.version} (clienta {self.user_id})'

    # ─── Derivados (solo lectura) ─────────────────────────────────────────────
    @property
    def age(self):
        return dates.age_today(self.birth_date)

    @property
    def needs_confirmation(self):
        return self.confirmed_by_client_at is None

    @property
    def sex_label(self):
        return self.get_sex_for_calculation_display()

    @property
    def conditions_labels(self):
        return ch.labels_for(ch.CONDITION_CHOICES, self.conditions)

    @property
    def medications_labels(self):
        return ch.labels_for(ch.MEDICATION_CHOICES, self.medications)

    @property
    def injuries_labels(self):
        return ch.labels_for(ch.INJURY_CHOICES, self.injuries)

    @property
    def allergies_labels(self):
        return ch.labels_for(ch.ALLERGY_CHOICES, self.allergies)

    @property
    def intolerances_labels(self):
        return ch.labels_for(ch.INTOLERANCE_CHOICES, self.intolerances)

    @property
    def equipment_labels(self):
        return ch.labels_for(ch.EQUIPMENT_CHOICES, self.equipment)

    @property
    def meal_slots_labels(self):
        return ch.labels_for(ch.MEAL_SLOT_CHOICES, self.meal_slots)

    @property
    def meal_schedule(self):
        """[{code, label, time}] de las comidas elegidas, en el orden del día (time puede ser '')."""
        labels = dict(ch.MEAL_SLOT_CHOICES)
        times = self.meal_times or {}
        return [{'code': code, 'label': labels.get(code, code), 'time': times.get(code, '')}
                for code in self.meal_slots]

    @property
    def parq_answers(self):
        """[{key, number, text, answer}] con las 7 preguntas y la respuesta (True = Sí)."""
        answers = self.parq or {}
        return [{'key': key, 'number': index, 'text': text, 'answer': bool(answers.get(key))}
                for index, (key, text) in enumerate(ch.PARQ_QUESTIONS, start=1)]

    @property
    def parq_yes_count(self):
        return sum(1 for a in self.parq_answers if a['answer'])

    def clearance_is_valid(self, on=None):
        """Autorización médica vigente: menos de 12 meses exactos desde su fecha."""
        return rules_flags.clearance_is_valid(self.medical_clearance, self.clearance_date, on or dates.today())


class HealthAccessLog(models.Model):
    """Quién tocó la ficha de una clienta y cuándo (seguridad y acceso restringido, Ley 1581).
    Sin IP y sin contenido de la ficha. No se borra al borrar la ficha: es la evidencia del
    borrado; solo se va con la cuenta de la clienta."""
    ACTION_VIEW = 'view'
    ACTION_EDIT = 'edit'
    ACTION_EXPORT = 'export'
    ACTION_DELETE = 'delete'

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='health_access_actions', verbose_name='Quién',
    )
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='health_access_log', verbose_name='Clienta',
    )
    action = models.CharField(max_length=10, choices=ch.ACCESS_ACTION_CHOICES, verbose_name='Acción')
    profile_version = models.PositiveIntegerField(null=True, blank=True, verbose_name='Versión de la ficha')
    created_at = models.DateTimeField(default=timezone.now, verbose_name='Cuándo')

    class Meta:
        verbose_name = 'Acceso a la ficha de salud'
        verbose_name_plural = 'Accesos a la ficha de salud'
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['client', '-created_at'], name='health_access_client_idx')]

    def __str__(self):
        return f'{self.action} · clienta {self.client_id} · {self.created_at:%Y-%m-%d %H:%M}'
