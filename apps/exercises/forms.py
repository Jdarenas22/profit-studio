"""Formularios de ejercicios: crear/editar (con archivos) y filtros de las listas."""
from django import forms
from django.core.validators import MaxLengthValidator

from apps.accounts.form_utils import SafeImageField, SafeVideoField

from .models import EQUIPMENT_CHOICES, INTENSITY_CHOICES, RISK_TAG_CHOICES, Exercise, ExerciseCategory
from .validators import INVALID_MESSAGE, validate_youtube_url


class ExerciseForm(forms.ModelForm):
    """Crear/editar ejercicio.

    - `video_url`: solo enlaces HTTPS de YouTube (youtube.com, youtu.be, youtube-nocookie.com).
    - `image`: imagen real JPG/PNG/WEBP/GIF, máx. 5 MB (se abre con Pillow).
    - `video_file`: MP4/WEBM/MOV, máx. 100 MB, con content-type y firma de archivo coherentes.
    - `level` y `category` salen de las opciones/filas existentes (ModelForm).
    - `equipment`, `risk_tags` e `intensity`: etiquetas opcionales para los planes automáticos
      (solo códigos de las listas cerradas; vacío = sin etiquetar).
    """

    video_url = forms.CharField(
        label='Enlace de YouTube', required=False, max_length=200,
        validators=[validate_youtube_url],
        error_messages={'max_length': INVALID_MESSAGE},
    )
    video_file = SafeVideoField(label='Video (archivo)', required=False)
    image = SafeImageField(label='Imagen de portada', required=False)
    equipment = forms.MultipleChoiceField(
        label='Equipo necesario', required=False, choices=EQUIPMENT_CHOICES,
        error_messages={'invalid_choice': 'Selecciona un equipo válido.'},
    )
    risk_tags = forms.MultipleChoiceField(
        label='Zonas de riesgo', required=False, choices=RISK_TAG_CHOICES,
        error_messages={'invalid_choice': 'Selecciona una zona de riesgo válida.'},
    )
    intensity = forms.ChoiceField(
        label='Intensidad', required=False, choices=[('', '— Sin etiquetar —')] + INTENSITY_CHOICES,
        error_messages={'invalid_choice': 'Selecciona una intensidad válida.'},
    )

    class Meta:
        model = Exercise
        fields = ['name', 'category', 'level', 'description', 'muscles', 'recommendations',
                  'video_url', 'video_file', 'image', 'is_public', 'equipment', 'risk_tags', 'intensity']
        labels = {
            'name': 'Nombre', 'category': 'Categoría', 'level': 'Nivel',
            'description': 'Descripción', 'muscles': 'Músculos trabajados',
            'recommendations': 'Recomendaciones', 'is_public': 'Visible en la galería pública',
        }
        error_messages = {
            'name': {'required': 'El nombre del ejercicio es obligatorio.',
                     'max_length': 'El nombre admite máximo 200 caracteres.'},
            'category': {'invalid_choice': 'Selecciona una categoría válida.'},
            'level': {'required': 'Selecciona el nivel.', 'invalid_choice': 'Selecciona un nivel válido.'},
            'description': {'required': 'La descripción es obligatoria.'},
            'muscles': {'required': 'Indica los músculos trabajados.'},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].required = False
        self.fields['category'].empty_label = '— Sin categoría —'
        for name, limit in (('description', 5000), ('muscles', 1000), ('recommendations', 3000)):
            self.fields[name].validators.append(
                MaxLengthValidator(limit, message=f'Máximo {limit} caracteres.'))

    def clean_video_url(self):
        return self.cleaned_data.get('video_url', '').strip()

    def clean(self):
        cleaned = super().clean()
        # Equipo o zonas sin intensidad = etiquetado a medias: el motor lo trataría como
        # "sin etiquetar" sin que la entrenadora lo note.
        if (cleaned.get('equipment') or cleaned.get('risk_tags')) and not cleaned.get('intensity'):
            self.add_error('intensity', 'Indica la intensidad para que el ejercicio quede etiquetado.')
        return cleaned


class ExerciseFilterForm(forms.Form):
    """Filtros por GET de las listas. Un valor inválido (p. ej. ?category=abc) se ignora."""

    category = forms.ModelChoiceField(queryset=ExerciseCategory.objects.all(), required=False)
    level = forms.ChoiceField(choices=[('', '')] + list(Exercise.LEVEL_CHOICES), required=False)

    def cleaned_filters(self):
        """(categoría|None, nivel|'') ya validados; lo inválido queda como "sin filtro"."""
        self.is_valid()
        category = self.cleaned_data.get('category')
        level = self.cleaned_data.get('level') or ''
        return category, level
