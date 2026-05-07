from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    ROLE_TRAINER = 'trainer'
    ROLE_MEMBER = 'member'
    ROLE_CHOICES = [
        (ROLE_TRAINER, 'Entrenadora'),
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
