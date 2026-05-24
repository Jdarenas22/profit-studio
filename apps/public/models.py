from django.db import models


class Testimonial(models.Model):
    """Testimonio de un cliente real para mostrar en la página pública."""
    name = models.CharField(max_length=100, verbose_name='Nombre')
    role = models.CharField(
        max_length=150, blank=True,
        verbose_name='Logro / Descripción',
        help_text='Ej: "Perdió 10 kg en 3 meses" o "Cliente desde 2023"',
    )
    text = models.TextField(verbose_name='Testimonio')
    photo = models.ImageField(
        upload_to='testimonials/', blank=True, null=True,
        verbose_name='Foto (opcional)',
    )
    rating = models.PositiveSmallIntegerField(
        default=5, verbose_name='Estrellas (1–5)',
    )
    is_active = models.BooleanField(default=True, verbose_name='Mostrar en la web')
    order = models.PositiveIntegerField(default=0, verbose_name='Orden de aparición')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Testimonio'
        verbose_name_plural = 'Testimonios'
        ordering = ['order', '-created_at']

    def __str__(self):
        return f"Testimonio — {self.name}"

    @property
    def initials(self):
        parts = self.name.strip().split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        return self.name[:2].upper()
