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

    # Video por URL de YouTube (preferido en producción — no requiere almacenamiento)
    video_url = models.URLField(
        blank=True, default='',
        verbose_name='URL de video (YouTube)',
        help_text='Pega el enlace de YouTube. Ej: https://youtu.be/abc123 o https://youtube.com/watch?v=abc123',
    )
    # Archivo de video — solo funciona bien en local; en Railway se pierde con cada redeploy
    video_file = models.FileField(
        upload_to='exercises/videos/',
        blank=True, null=True,
        verbose_name='Video (archivo)',
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

    @property
    def youtube_embed_url(self):
        """Convierte cualquier URL de YouTube al formato de embed."""
        import re
        url = self.video_url
        if not url:
            return ''
        patterns = [
            r'youtu\.be/([^?&\s]+)',
            r'youtube\.com/watch\?v=([^&\s]+)',
            r'youtube\.com/embed/([^?&\s]+)',
            r'youtube\.com/shorts/([^?&\s]+)',
        ]
        for pattern in patterns:
            m = re.search(pattern, url)
            if m:
                return f'https://www.youtube.com/embed/{m.group(1)}?rel=0&modestbranding=1'
        return url  # fallback: devuelve la URL tal cual

    def __str__(self):
        return self.name
