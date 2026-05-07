from django.db import models


class ExerciseCategory(models.Model):
    name = models.CharField(max_length=100, verbose_name='Nombre')
    order = models.PositiveIntegerField(default=0, verbose_name='Orden')

    class Meta:
        verbose_name = 'Categoría'
        verbose_name_plural = 'Categorías'
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class Exercise(models.Model):
    LEVEL_BEGINNER = 'beginner'
    LEVEL_INTERMEDIATE = 'intermediate'
    LEVEL_ADVANCED = 'advanced'
    LEVEL_CHOICES = [
        (LEVEL_BEGINNER, 'Principiante'),
        (LEVEL_INTERMEDIATE, 'Intermedio'),
        (LEVEL_ADVANCED, 'Avanzado'),
    ]

    name = models.CharField(max_length=200, verbose_name='Nombre')
    category = models.ForeignKey(
        ExerciseCategory,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='exercises',
        verbose_name='Categoría',
    )
    level = models.CharField(
        max_length=15, choices=LEVEL_CHOICES,
        verbose_name='Nivel'
    )
    description = models.TextField(verbose_name='Descripción')
    muscles = models.TextField(verbose_name='Músculos trabajados')
    recommendations = models.TextField(blank=True, verbose_name='Recomendaciones')

    # Archivos — en producción se almacenan en Cloudflare R2 con URLs firmadas
    video_file = models.FileField(
        upload_to='exercises/videos/',
        blank=True, null=True,
        verbose_name='Video',
    )
    image = models.ImageField(
        upload_to='exercises/images/',
        blank=True, null=True,
        verbose_name='Imagen',
    )

    is_public = models.BooleanField(default=False, verbose_name='Público')
    is_active = models.BooleanField(default=True, verbose_name='Activo')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Ejercicio'
        verbose_name_plural = 'Ejercicios'
        ordering = ['name']

    def __str__(self):
        return self.name
