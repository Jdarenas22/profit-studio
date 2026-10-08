from django.db import models

from .validators import extract_youtube_id, validate_youtube_url


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
        validators=[validate_youtube_url],
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
        """URL de embed de YouTube, o '' si `video_url` no es un enlace HTTPS de YouTube.

        Nunca devuelve la URL original: lo que acaba dentro de un <iframe> siempre se
        reconstruye a partir del ID de video validado.
        """
        video_id = extract_youtube_id(self.video_url)
        if not video_id:
            return ''
        return f'https://www.youtube.com/embed/{video_id}?rel=0&modestbranding=1'

    @property
    def safe_video_url(self):
        """Enlace para abrir el video en YouTube (href), o '' si la URL guardada no es válida.

        Las plantillas deberían usar esta propiedad en lugar de `video_url` dentro de un href:
        así un valor antiguo como `javascript:...` guardado antes de esta validación no se pinta.
        """
        video_id = extract_youtube_id(self.video_url)
        return f'https://www.youtube.com/watch?v={video_id}' if video_id else ''

    def __str__(self):
        return self.name
