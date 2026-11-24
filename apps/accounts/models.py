from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from . import choices

class User(AbstractUser):
    ROLE_TRAINER = 'trainer'
    ROLE_MEMBER = 'member'
    ROLE_CHOICES = [
        (ROLE_TRAINER, 'Entrenador/a'),
        (ROLE_MEMBER, 'Miembro'),
    ]

    GENDER_FEMALE = 'F'
    GENDER_MALE = 'M'
    GENDER_OTHER = 'O'
    GENDER_CHOICES = [
        (GENDER_FEMALE, 'Femenino'),
        (GENDER_MALE, 'Masculino'),
        (GENDER_OTHER, 'Prefiero no decir'),
    ]

    GOAL_WEIGHT_LOSS = 'weight_loss'
    GOAL_MUSCLE_GAIN = 'muscle_gain'
    GOAL_CARDIO = 'cardio'
    GOAL_REHAB = 'rehab'
    GOAL_SPORTS = 'sports'
    GOAL_TONING = 'toning'
    GOAL_CHOICES = [
        (GOAL_WEIGHT_LOSS, 'Pérdida de peso'),
        (GOAL_MUSCLE_GAIN, 'Ganancia muscular'),
        (GOAL_CARDIO, 'Resistencia cardiovascular'),
        (GOAL_REHAB, 'Rehabilitación / Salud general'),
        (GOAL_SPORTS, 'Rendimiento deportivo'),
        (GOAL_TONING, 'Tonificación y definición'),
    ]

    role = models.CharField(
        max_length=10, choices=ROLE_CHOICES, default=ROLE_MEMBER,
        verbose_name='Rol'
    )
    gender = models.CharField(
        max_length=1, choices=GENDER_CHOICES, blank=True, default='',
        verbose_name='Género'
    )
    phone = models.CharField(max_length=20, blank=True, verbose_name='Teléfono')
    profile_photo = models.ImageField(
        upload_to='profiles/', blank=True, null=True,
        verbose_name='Foto de perfil'
    )
    interested_plan = models.ForeignKey(
        'memberships.MembershipPlan',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='interested_users',
        verbose_name='Plan de interés',
    )
    training_goal = models.CharField(
        max_length=20, choices=GOAL_CHOICES, blank=True, default='',
        verbose_name='Meta de entrenamiento',
    )
    assigned_trainer = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='assigned_clients',
        verbose_name='Entrenador/a asignado/a',
        limit_choices_to={'role': 'trainer'},
    )
    bio = models.TextField(
        blank=True, default='',
        verbose_name='Descripción / Bio',
        help_text='Descripción breve del entrenador (aparece en la página Nosotros)',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Ciclo de vida (docs/contracts/cuentas-eliminar.md): desactivada = is_active False + fecha;
    # eliminada = lápida sin datos personales (erased_at). Solo los cambia apps/accounts/services.py.
    deactivated_at = models.DateTimeField(null=True, blank=True, verbose_name='Desactivada el')
    erased_at = models.DateTimeField(null=True, blank=True, verbose_name='Eliminada el')

    class Meta:
        verbose_name = 'Usuario'
        verbose_name_plural = 'Usuarios'
        constraints = [
            # Una cuenta eliminada nunca puede volver a estar activa
            models.CheckConstraint(
                condition=models.Q(erased_at__isnull=True) | models.Q(is_active=False),
                name='accounts_user_erased_is_inactive',
            ),
        ]

    @property
    def is_trainer(self):
        return self.role == self.ROLE_TRAINER

    @property
    def has_active_membership(self):
        try:
            return self.membership.is_valid
        except ObjectDoesNotExist:
            return False

    @property
    def is_erased(self):
        return self.erased_at is not None

    @property
    def display_name(self):
        """Nombre para mostrar: «Cuenta eliminada» en una lápida; si no, el nombre o el usuario."""
        if self.is_erased:
            return choices.ERASED_LABEL
        return self.get_full_name() or self.username

    def __str__(self):
        return f"{self.display_name} ({self.get_role_display()})"


class AccountAuditLog(models.Model):
    """Quién desactivó, reactivó, eliminó o traspasó la administración a qué cuenta y cuándo
    (sección 11 de docs/contracts/cuentas-eliminar.md y docs/contracts/transferir-administracion.md).

    Sin nombres, correos, IP ni texto libre: solo ids, rol, acción, motivo (lista cerrada) y
    conteos. Las claves son PROTECT: las superusuarias no se eliminan y el resto queda como
    lápida, así que nunca se pierde la referencia. Solo lectura en el admin.
    """
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name='account_actions',
                              verbose_name='Quién')
    target = models.ForeignKey(User, on_delete=models.PROTECT, related_name='account_audit',
                               verbose_name='Cuenta')
    target_role = models.CharField(max_length=10, choices=User.ROLE_CHOICES, verbose_name='Rol de la cuenta')
    action = models.CharField(max_length=20, choices=choices.ACTION_CHOICES, verbose_name='Acción')
    reason = models.CharField(max_length=20, choices=choices.REASON_CHOICES, blank=True, default='',
                              verbose_name='Motivo')
    reassigned_to = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True,
                                      related_name='account_reassignments', verbose_name='Clientas pasaron a')
    reassigned_count = models.PositiveIntegerField(default=0, verbose_name='Clientas reasignadas')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Cuándo')

    class Meta:
        verbose_name = 'Registro de cuentas'
        verbose_name_plural = 'Registro de cuentas (auditoría)'
        ordering = ['-created_at', '-pk']

    def __str__(self):
        return f'{self.get_action_display()} · cuenta {self.target_id} · {self.created_at:%Y-%m-%d %H:%M}'
