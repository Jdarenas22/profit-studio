from django.contrib.auth.models import AbstractUser
from django.db import models


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

    class Meta:
        verbose_name = 'Usuario'
        verbose_name_plural = 'Usuarios'

    @property
    def is_trainer(self):
        return self.role == self.ROLE_TRAINER

    @property
    def has_active_membership(self):
        try:
            return self.membership.is_valid
        except Exception:
            return False

    def __str__(self):
        full = self.get_full_name()
        return f"{full or self.username} ({self.get_role_display()})"
