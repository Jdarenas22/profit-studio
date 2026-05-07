from django.db import models
from django.conf import settings


class Routine(models.Model):
    name = models.CharField(max_length=200, verbose_name='Nombre')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='routines',
        verbose_name='Cliente',
    )
    trainer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_routines',
        verbose_name='Entrenadora',
    )
    is_active = models.BooleanField(default=True, verbose_name='Activa')
    notes = models.TextField(blank=True, verbose_name='Notas generales')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Rutina'
        verbose_name_plural = 'Rutinas'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} — {self.user}"


class RoutineDay(models.Model):
    routine = models.ForeignKey(
        Routine,
        on_delete=models.CASCADE,
        related_name='days',
        verbose_name='Rutina',
    )
    name = models.CharField(max_length=100, verbose_name='Nombre del día')
    order = models.PositiveIntegerField(default=0, verbose_name='Orden')

    class Meta:
        verbose_name = 'Día de rutina'
        verbose_name_plural = 'Días de rutina'
        ordering = ['order']

    def __str__(self):
        return f"{self.routine.name} — {self.name}"


class RoutineExercise(models.Model):
    day = models.ForeignKey(
        RoutineDay,
        on_delete=models.CASCADE,
        related_name='exercises',
        verbose_name='Día',
    )
    exercise = models.ForeignKey(
        'exercises.Exercise',
        on_delete=models.CASCADE,
        verbose_name='Ejercicio',
    )
    sets = models.PositiveIntegerField(verbose_name='Series')
    reps = models.CharField(max_length=50, verbose_name='Repeticiones')
    rest_seconds = models.PositiveIntegerField(verbose_name='Descanso (seg)')
    observations = models.TextField(blank=True, verbose_name='Observaciones')
    order = models.PositiveIntegerField(default=0, verbose_name='Orden')

    class Meta:
        verbose_name = 'Ejercicio en rutina'
        verbose_name_plural = 'Ejercicios en rutina'
        ordering = ['order']

    def __str__(self):
        return f"{self.exercise.name} — {self.day}"
