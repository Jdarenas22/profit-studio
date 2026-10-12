"""Planes generados con IA (fase B de docs/contracts/plan-ia.md: plan de ejercicio).

- `Plan`: una versión de un plan de una clienta (`exercise` en la fase B; `nutrition` llega en la
  fase C). Estados: `draft_ai` -> `in_review` -> `approved` -> `superseded`/`archived`. Un plan
  aprobado no se edita. La clienta solo ve planes `approved`.
- `PlanGenerationJob`: un pedido de generación (cola en la base de datos). El intento fallido o
  bloqueado vive aquí (solo códigos), nunca como `Plan`: la salida no validada no se guarda.
- `PlanEvent`: historial de lo que pasó con un plan (quién, qué, cuándo).
- `TrainerCredential`: quién puede validar planes de alimentación (fase C) y la frase de su
  credencial que se imprime en el plan.
- Fase C (alimentación): `Food` (catálogo; la IA elige ids y gramos, Python calcula todo),
  `NutritionPlan` (objetivos calculados) -> `MealSlot` (comidas del día) -> `MealOption` (3
  opciones equivalentes por comida) -> `MealOptionItem`, y `MealChoice` (lo que la clienta elige
  cada día).

Ninguno guarda nombre, correo ni datos de la ficha: solo ids, códigos y lo que la IA devolvió
ya validado (convertido en `Routine` o en opciones de comida). Solo `TrainerCredential` y `Food`
están en el admin (solo la superusuaria).
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from .rules import foods as foods_rules

KIND_EXERCISE = 'exercise'
KIND_NUTRITION = 'nutrition'
KIND_CHOICES = [
    (KIND_EXERCISE, 'Ejercicio'),
    (KIND_NUTRITION, 'Alimentación'),
]
GROUP_CHOICES = foods_rules.GROUPS


class Plan(models.Model):
    STATUS_DRAFT_AI = 'draft_ai'
    STATUS_IN_REVIEW = 'in_review'
    STATUS_APPROVED = 'approved'
    STATUS_SUPERSEDED = 'superseded'
    STATUS_ARCHIVED = 'archived'
    STATUS_CHOICES = [
        (STATUS_DRAFT_AI, 'Borrador de la IA'),
        (STATUS_IN_REVIEW, 'En revisión'),
        (STATUS_APPROVED, 'Aprobado'),
        (STATUS_SUPERSEDED, 'Reemplazado'),
        (STATUS_ARCHIVED, 'Archivado'),
    ]
    PENDING_STATUSES = (STATUS_DRAFT_AI, STATUS_IN_REVIEW)

    client = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='plans', verbose_name='Clienta',
    )
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, verbose_name='Tipo')
    version = models.PositiveIntegerField(verbose_name='Versión')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_DRAFT_AI, verbose_name='Estado')
    parent = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='children',
        verbose_name='Versión anterior',
    )
    # Ejercicio: rutina real, inactiva hasta aprobar (las vistas de la clienta filtran is_active=True)
    routine = models.OneToOneField(
        'routines.Routine', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='plan', verbose_name='Rutina',
    )
    health_profile = models.ForeignKey(
        'health.HealthProfile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Ficha usada',
    )
    profile_version = models.PositiveIntegerField(null=True, blank=True, verbose_name='Versión de la ficha usada')
    measurement = models.ForeignKey(
        'assessments.BodyMeasurement', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Medición base',
    )
    flags = models.JSONField(default=list, blank=True, verbose_name='Advertencias (códigos)')
    warnings_ack_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Revisó las advertencias',
    )
    warnings_ack_at = models.DateTimeField(null=True, blank=True)
    is_simulated = models.BooleanField(default=False, verbose_name='Modo de prueba')
    model_name = models.CharField(max_length=80, blank=True, verbose_name='Modelo de IA')
    prompt_version = models.CharField(max_length=40, blank=True)
    ruleset_version = models.CharField(max_length=40, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='En revisión por',
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Aprobado por',
    )
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name='Aprobado el')
    reviewer_credential = models.CharField(max_length=200, blank=True, verbose_name='Credencial de quien aprobó')
    note_for_client = models.CharField(max_length=500, blank=True, verbose_name='Nota para la clienta')
    internal_notes = models.TextField(blank=True, verbose_name='Notas internas')
    valid_until = models.DateField(null=True, blank=True, verbose_name='Vigente hasta')
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, verbose_name='Creado')

    class Meta:
        verbose_name = 'Plan'
        verbose_name_plural = 'Planes'
        ordering = ['-created_at', '-pk']
        constraints = [
            models.UniqueConstraint(fields=['client', 'kind', 'version'], name='plans_plan_unique_version'),
            # Un solo plan aprobado por clienta y tipo (la integridad no depende solo del código)
            models.UniqueConstraint(
                fields=['client', 'kind'], condition=models.Q(status='approved'),
                name='plans_plan_one_approved',
            ),
        ]

    def __str__(self):
        return f'Plan {self.kind} v{self.version} (clienta {self.client_id})'

    @property
    def is_pending(self):
        return self.status in self.PENDING_STATUSES

    @property
    def is_approved(self):
        return self.status == self.STATUS_APPROVED


class PlanGenerationJob(models.Model):
    KIND_MEAL_ALTERNATIVE = 'meal_alternative'
    JOB_KIND_CHOICES = KIND_CHOICES + [(KIND_MEAL_ALTERNATIVE, 'Otra opción de comida')]

    STATUS_QUEUED = 'queued'
    STATUS_RUNNING = 'running'
    STATUS_SUCCEEDED = 'succeeded'
    STATUS_FAILED = 'failed'
    STATUS_BLOCKED = 'blocked'
    STATUS_CHOICES = [
        (STATUS_QUEUED, 'En cola'),
        (STATUS_RUNNING, 'Generando'),
        (STATUS_SUCCEEDED, 'Listo'),
        (STATUS_FAILED, 'Falló'),
        (STATUS_BLOCKED, 'Bloqueado por seguridad'),
    ]
    ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_RUNNING)
    FINAL_STATUSES = (STATUS_SUCCEEDED, STATUS_FAILED, STATUS_BLOCKED)

    ROLE_MEMBER = 'member'
    ROLE_TRAINER = 'trainer'
    ROLE_CHOICES = [(ROLE_MEMBER, 'Clienta'), (ROLE_TRAINER, 'Entrenador/a')]

    kind = models.CharField(max_length=20, choices=JOB_KIND_CHOICES, verbose_name='Tipo')
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='plan_jobs', verbose_name='Clienta',
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Pedido por',
    )
    requested_by_role = models.CharField(max_length=10, choices=ROLE_CHOICES, verbose_name='Rol de quien lo pidió')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_QUEUED, verbose_name='Estado')
    plan = models.ForeignKey(
        Plan, on_delete=models.SET_NULL, null=True, blank=True, related_name='jobs', verbose_name='Plan creado',
    )
    parent_plan = models.ForeignKey(
        Plan, on_delete=models.SET_NULL, null=True, blank=True, related_name='regeneration_jobs',
        verbose_name='Plan que se regenera',
    )
    # Idempotencia: el formulario lleva un UUID; recargar o doble clic no crea otro trabajo
    request_token = models.UUIDField(unique=True, verbose_name='Token del pedido')
    # Identificador aleatorio que se envía a la IA (nunca el id de la clienta)
    job_ref = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    health_profile = models.ForeignKey(
        'health.HealthProfile', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    attempts = models.PositiveSmallIntegerField(default=0, verbose_name='Intentos')
    max_attempts = models.PositiveSmallIntegerField(default=3)
    locked_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, verbose_name='Creado')
    error_code = models.CharField(max_length=80, blank=True, verbose_name='Código de error')
    flags = models.JSONField(default=list, blank=True, verbose_name='Banderas (códigos)')
    progress_done = models.PositiveSmallIntegerField(default=0)
    progress_total = models.PositiveSmallIntegerField(default=1)
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    trainer_notes = models.CharField(max_length=500, blank=True, verbose_name='Notas del entrenador')
    # "Otra opción" (fase C): la comida y la opción que la clienta no quiere
    slot = models.ForeignKey(
        'plans.MealSlot', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        verbose_name='Comida',
    )
    replaces_option = models.ForeignKey(
        'plans.MealOption', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        verbose_name='Opción que se reemplaza',
    )

    class Meta:
        verbose_name = 'Trabajo de generación'
        verbose_name_plural = 'Trabajos de generación'
        ordering = ['-created_at', '-pk']
        constraints = [
            # Un solo trabajo en curso por clienta y tipo
            models.UniqueConstraint(
                fields=['client', 'kind'], condition=models.Q(status__in=['queued', 'running']),
                name='plans_job_one_active',
            ),
        ]
        indexes = [models.Index(fields=['status', 'created_at'], name='plans_job_status_idx')]

    def __str__(self):
        return f'Trabajo {self.kind} #{self.pk} ({self.status})'

    @property
    def is_finished(self):
        return self.status in self.FINAL_STATUSES


class PlanEvent(models.Model):
    ACTION_CREATED = 'created'
    ACTION_TAKEN = 'taken_for_review'
    ACTION_EDITED = 'edited'
    ACTION_REGENERATE = 'regenerate_requested'
    ACTION_APPROVED = 'approved'
    ACTION_ARCHIVED = 'archived'
    ACTION_SUPERSEDED = 'superseded'
    ACTION_CLONED = 'cloned'
    ACTION_CLIENT_ALTERNATIVE = 'client_alternative'
    ACTION_CHOICES = [
        (ACTION_CREATED, 'Creado'),
        (ACTION_TAKEN, 'Tomado para revisión'),
        (ACTION_EDITED, 'Editado'),
        (ACTION_REGENERATE, 'Regeneración pedida'),
        (ACTION_APPROVED, 'Aprobado'),
        (ACTION_ARCHIVED, 'Archivado'),
        (ACTION_SUPERSEDED, 'Reemplazado por una versión nueva'),
        (ACTION_CLONED, 'Clonado a borrador'),
        (ACTION_CLIENT_ALTERNATIVE, 'Otra opción pedida por la clienta'),
    ]

    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name='events', verbose_name='Plan')
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Quién',
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, verbose_name='Acción')
    note = models.CharField(max_length=500, blank=True, verbose_name='Nota')
    at = models.DateTimeField(default=timezone.now, verbose_name='Cuándo')

    class Meta:
        verbose_name = 'Evento de plan'
        verbose_name_plural = 'Eventos de plan'
        ordering = ['-at', '-pk']

    def __str__(self):
        return f'{self.get_action_display()} — plan {self.plan_id}'


class TrainerCredential(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='plan_credential',
        verbose_name='Entrenador/a', limit_choices_to={'role': 'trainer'},
    )
    can_validate_nutrition = models.BooleanField(
        default=False, verbose_name='Puede validar planes de alimentación',
        help_text='Solo nutricionista-dietista con registro en el RETHUS (Ley 1164 de 2007).',
    )
    registration_label = models.CharField(
        max_length=200, blank=True, verbose_name='Credencial que se imprime en el plan',
        help_text='Ej.: Nutricionista-dietista, RETHUS N.º 12345',
    )

    class Meta:
        verbose_name = 'Credencial de validación'
        verbose_name_plural = 'Credenciales de validación'

    def __str__(self):
        return f'Credencial de {self.user}'


# ─── Fase C: plan de alimentación ─────────────────────────────────────────────

class Food(models.Model):
    """Alimento del catálogo (valores por 100 g). La IA solo elige ids de aquí y gramos; las kcal,
    los macros y las medidas caseras los calcula Python. Sin `validated_by` el alimento solo se
    usa en DEBUG: un profesional debe validar las cifras antes de usarlas con clientas reales."""
    name = models.CharField(max_length=120, unique=True, verbose_name='Nombre')
    group = models.CharField(max_length=20, choices=GROUP_CHOICES, verbose_name='Grupo')
    kcal100 = models.DecimalField(max_digits=6, decimal_places=1, verbose_name='kcal por 100 g')
    protein100 = models.DecimalField(max_digits=5, decimal_places=1, verbose_name='Proteína (g) por 100 g')
    carbs100 = models.DecimalField(max_digits=5, decimal_places=1, verbose_name='Carbohidratos (g) por 100 g')
    fat100 = models.DecimalField(max_digits=5, decimal_places=1, verbose_name='Grasa (g) por 100 g')
    fiber100 = models.DecimalField(max_digits=5, decimal_places=1, default=0, verbose_name='Fibra (g) por 100 g')
    household_unit = models.CharField(
        max_length=60, blank=True, verbose_name='Medida casera (singular)', help_text='Ej.: taza cocida')
    household_unit_plural = models.CharField(
        max_length=60, blank=True, verbose_name='Medida casera (plural)', help_text='Ej.: tazas cocidas')
    household_grams = models.DecimalField(
        max_digits=6, decimal_places=1, null=True, blank=True, verbose_name='Gramos de una medida casera')
    allergen_tags = models.JSONField(default=list, blank=True, verbose_name='Alérgenos que contiene')
    trace_tags = models.JSONField(default=list, blank=True, verbose_name='Alérgenos posibles por trazas')
    diet_tags = models.JSONField(default=list, blank=True, verbose_name='Dietas en las que cabe')
    condition_tags = models.JSONField(default=list, blank=True, verbose_name='Etiquetas de condición')
    source = models.CharField(max_length=200, blank=True, verbose_name='Fuente de las cifras')
    validated_by = models.CharField(
        max_length=200, blank=True, verbose_name='Validado por',
        help_text='Quién verificó las cifras (nombre y credencial). Vacío = sin validar.')
    is_active = models.BooleanField(default=True, verbose_name='Activo')
    is_common = models.BooleanField(default=True, verbose_name='Común y de fácil acceso')

    class Meta:
        verbose_name = 'Alimento'
        verbose_name_plural = 'Alimentos'
        ordering = ['group', 'name']

    def __str__(self):
        return self.name


class NutritionPlan(models.Model):
    """Objetivos diarios CALCULADOS (Mifflin-St Jeor) de un `Plan` de alimentación."""
    plan = models.OneToOneField(Plan, on_delete=models.CASCADE, related_name='nutrition', verbose_name='Plan')
    kcal_target = models.PositiveIntegerField(verbose_name='Energía objetivo (kcal)')
    protein_g = models.PositiveSmallIntegerField(verbose_name='Proteína (g)')
    carbs_g = models.PositiveSmallIntegerField(verbose_name='Carbohidratos (g)')
    fat_g = models.PositiveSmallIntegerField(verbose_name='Grasa (g)')
    fiber_g = models.PositiveSmallIntegerField(verbose_name='Fibra mínima (g)')
    tmb = models.DecimalField(max_digits=7, decimal_places=1, verbose_name='Tasa metabólica basal')
    get = models.PositiveIntegerField(verbose_name='Gasto energético total')
    activity_factor = models.DecimalField(max_digits=4, decimal_places=3, verbose_name='Factor de actividad')
    adjustment_pct = models.DecimalField(max_digits=5, decimal_places=1, default=0, verbose_name='Ajuste (% del gasto)')
    weekly_change_kg = models.DecimalField(max_digits=4, decimal_places=2, default=0, verbose_name='Cambio semanal estimado (kg)')
    floor_applied = models.BooleanField(default=False, verbose_name='Se aplicó el piso calórico')
    warnings = models.JSONField(default=list, blank=True, verbose_name='Avisos del cálculo (códigos)')
    meals_per_day = models.PositiveSmallIntegerField(verbose_name='Comidas al día')
    formula = models.CharField(max_length=40, verbose_name='Fórmula')

    class Meta:
        verbose_name = 'Plan de alimentación'
        verbose_name_plural = 'Planes de alimentación'

    def __str__(self):
        return f'Objetivos del plan {self.plan_id}: {self.kcal_target} kcal'


class MealSlot(models.Model):
    nutrition_plan = models.ForeignKey(NutritionPlan, on_delete=models.CASCADE, related_name='slots')
    code = models.CharField(max_length=12, verbose_name='Código')
    name = models.CharField(max_length=40, verbose_name='Comida')
    time_hint = models.CharField(max_length=10, blank=True, verbose_name='Hora sugerida')
    order = models.PositiveSmallIntegerField(verbose_name='Orden')
    kcal_target = models.PositiveIntegerField(verbose_name='Energía objetivo (kcal)')
    protein_g = models.PositiveSmallIntegerField(verbose_name='Proteína (g)')
    carbs_g = models.PositiveSmallIntegerField(verbose_name='Carbohidratos (g)')
    fat_g = models.PositiveSmallIntegerField(verbose_name='Grasa (g)')

    class Meta:
        ordering = ['order']
        constraints = [models.UniqueConstraint(fields=['nutrition_plan', 'code'], name='plans_slot_unique_code')]

    def __str__(self):
        return f'{self.name} (plan {self.nutrition_plan.plan_id})'


class MealOption(models.Model):
    ORIGIN_AI = 'ai'
    ORIGIN_TRAINER = 'trainer'
    ORIGIN_CLIENT_REGEN = 'client_regen'
    ORIGIN_CHOICES = [
        (ORIGIN_AI, 'IA'), (ORIGIN_TRAINER, 'Editada por el entrenador'),
        (ORIGIN_CLIENT_REGEN, 'Otra opción pedida por la clienta'),
    ]

    slot = models.ForeignKey(MealSlot, on_delete=models.CASCADE, related_name='options')
    position = models.PositiveSmallIntegerField(verbose_name='Posición (1 a 3)')
    name = models.CharField(max_length=120, verbose_name='Nombre')
    preparation = models.CharField(max_length=500, blank=True, verbose_name='Preparación')
    # Calculados por Python desde `Food` (nunca los escribe la IA)
    kcal = models.DecimalField(max_digits=6, decimal_places=1, verbose_name='kcal')
    protein_g = models.DecimalField(max_digits=5, decimal_places=1, verbose_name='Proteína (g)')
    carbs_g = models.DecimalField(max_digits=5, decimal_places=1, verbose_name='Carbohidratos (g)')
    fat_g = models.DecimalField(max_digits=5, decimal_places=1, verbose_name='Grasa (g)')
    origin = models.CharField(max_length=14, choices=ORIGIN_CHOICES, default=ORIGIN_AI, verbose_name='Origen')
    needs_review = models.BooleanField(default=False, verbose_name='Pendiente de revisión del entrenador')
    out_of_band = models.BooleanField(default=False, verbose_name='Fuera de la banda de energía')
    is_active = models.BooleanField(default=True, verbose_name='Activa')
    replaces = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='replaced_by',
        verbose_name='Reemplaza a',
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['slot', 'position', 'pk']
        constraints = [
            # Tres opciones activas como máximo por comida, una por posición
            models.UniqueConstraint(
                fields=['slot', 'position'], condition=models.Q(is_active=True), name='plans_option_one_per_position'),
        ]

    def __str__(self):
        return f'{self.name} ({self.slot_id})'


class MealOptionItem(models.Model):
    option = models.ForeignKey(MealOption, on_delete=models.CASCADE, related_name='items')
    food = models.ForeignKey(Food, on_delete=models.PROTECT, related_name='+', verbose_name='Alimento')
    grams = models.PositiveSmallIntegerField(verbose_name='Gramos')
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['order', 'pk']


class MealChoice(models.Model):
    """La opción que la clienta eligió para una comida de un día (adherencia en la fase D)."""
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='meal_choices', verbose_name='Clienta')
    slot = models.ForeignKey(MealSlot, on_delete=models.CASCADE, related_name='choices')
    option = models.ForeignKey(MealOption, on_delete=models.CASCADE, related_name='choices')
    date = models.DateField(verbose_name='Día')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='Marcada como comida')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['client', 'slot', 'date'], name='plans_choice_unique_day')]
        indexes = [models.Index(fields=['client', 'date'], name='plans_choice_client_date')]
