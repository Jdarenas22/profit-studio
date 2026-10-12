"""Planes generados con IA (fase B de docs/contracts/plan-ia.md: plan de ejercicio).

- `Plan`: una versión de un plan de una clienta (`exercise` en la fase B; `nutrition` llega en la
  fase C). Estados: `draft_ai` -> `in_review` -> `approved` -> `superseded`/`archived`. Un plan
  aprobado no se edita. La clienta solo ve planes `approved`.
- `PlanGenerationJob`: un pedido de generación (cola en la base de datos). El intento fallido o
  bloqueado vive aquí (solo códigos), nunca como `Plan`: la salida no validada no se guarda.
- `PlanEvent`: historial de lo que pasó con un plan (quién, qué, cuándo).
- `TrainerCredential`: quién puede validar planes de alimentación (fase C) y la frase de su
  credencial que se imprime en el plan.

Ninguno guarda nombre, correo ni datos de la ficha: solo ids, códigos y lo que la IA devolvió
ya validado (convertido en `Routine`). Solo `TrainerCredential` está en el admin.
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

KIND_EXERCISE = 'exercise'
KIND_NUTRITION = 'nutrition'
KIND_CHOICES = [
    (KIND_EXERCISE, 'Ejercicio'),
    (KIND_NUTRITION, 'Alimentación'),
]


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
