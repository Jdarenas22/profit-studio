"""Filtro del catálogo de ejercicios ANTES de llamar a la IA (contrato, sección 3.5).

Módulo PURO (sin Django ni red). La IA solo recibe los ejercicios permitidos, así no puede
proponer uno que choque con las lesiones, el equipo o las advertencias de la clienta: las
restricciones de salud se aplican aquí y a la IA no le llegan diagnósticos.

[A VALIDAR] Las equivalencias bandera -> etiqueta excluida son una propuesta técnica.
"""
from dataclasses import dataclass, field

LEVEL_ORDER = {'beginner': 1, 'intermediate': 2, 'advanced': 3}
INTENSITY_ORDER = {'low': 1, 'moderate': 2, 'high': 3}
# Experiencia de la ficha -> nivel máximo de ejercicio
EXPERIENCE_TO_LEVEL = {'none': 'beginner', 'beginner': 'beginner',
                       'intermediate': 'intermediate', 'advanced': 'advanced'}

MIN_ALLOWED_EXERCISES = 6        # menos que esto: D06 (el entrenador arma el plan a mano)
DEFAULT_MAX_SETS = 6
MAX_SETS_CONSERVATIVE = 3        # Y01 (hipertensión) y Y08 (70-79 años)

# Advertencias -> etiquetas de riesgo excluidas
_HIGH_IMPACT_FLAGS = {'Y04_JOINT', 'Y06_ANTICOAGULANTS', 'Y08_AGE_SENIOR', 'Y09_BMI_HIGH', 'R08_BMI_VERY_HIGH'}
_FLAG_EXCLUDES = {
    'Y01_HYPERTENSION': {'breath_hold'},
    'Y08_AGE_SENIOR': {'heavy_axial'},
}
# Condiciones articulares (de la ficha; NO se envían a la IA) -> zonas excluidas
_CONDITION_EXCLUDES = {
    'herniated_disc': {'lower_back', 'heavy_axial'},
    'osteoporosis': {'heavy_axial'},
}


@dataclass(frozen=True)
class ExerciseItem:
    """Lo mínimo de un `Exercise` (sin objetos de Django)."""
    id: int
    name: str
    category: str = ''
    level: str = 'beginner'
    equipment: tuple = ()
    risk_tags: tuple = ()
    intensity: str = ''          # '' = sin etiquetar

    @property
    def tagged(self):
        return bool(self.intensity)


@dataclass(frozen=True)
class TrainingProfile:
    """Lo de la ficha que afecta el filtro (incluye lesiones y condiciones: se usan aquí, en el
    servidor, y nunca viajan a la IA)."""
    experience_level: str = 'beginner'
    training_place: str = 'gym'
    equipment: tuple = ()
    injuries: tuple = ()
    conditions: tuple = ()
    yellow_codes: tuple = ()     # advertencias del alcance `exercise`


@dataclass(frozen=True)
class Limits:
    max_sets: int = DEFAULT_MAX_SETS
    max_intensity: str = 'high'
    excluded_tags: frozenset = field(default_factory=frozenset)


@dataclass(frozen=True)
class CatalogResult:
    allowed: tuple
    limits: Limits

    @property
    def allowed_ids(self):
        return frozenset(item.id for item in self.allowed)

    @property
    def enough(self):
        return len(self.allowed) >= MIN_ALLOWED_EXERCISES


def limits_for(profile):
    flags = set(profile.yellow_codes)
    excluded = set(profile.injuries)
    if flags & _HIGH_IMPACT_FLAGS:
        excluded.add('high_impact')
    for code, tags in _FLAG_EXCLUDES.items():
        if code in flags:
            excluded |= tags
    for condition in profile.conditions:
        excluded |= _CONDITION_EXCLUDES.get(condition, set())
    max_intensity = 'high'
    if 'Y03_THYROID_ASTHMA' in flags:
        max_intensity = 'moderate'
    if 'Y11_REHAB_GOAL' in flags:
        max_intensity = 'low'
    max_sets = MAX_SETS_CONSERVATIVE if flags & {'Y01_HYPERTENSION', 'Y08_AGE_SENIOR'} else DEFAULT_MAX_SETS
    return Limits(max_sets=max_sets, max_intensity=max_intensity, excluded_tags=frozenset(excluded))


def filter_exercises(items, profile):
    """Ejercicios permitidos para la clienta y los topes que la salida de la IA debe respetar."""
    limits = limits_for(profile)
    max_level = LEVEL_ORDER[EXPERIENCE_TO_LEVEL.get(profile.experience_level, 'beginner')]
    max_intensity = INTENSITY_ORDER[limits.max_intensity]
    at_home = profile.training_place == 'home'
    available = set(profile.equipment) - {'none'}
    # Prudencia: sin etiquetas no se sabe qué zonas carga ni qué equipo usa
    cautious = bool(profile.injuries or profile.yellow_codes or profile.conditions)

    allowed = []
    for item in items:
        if LEVEL_ORDER.get(item.level, 99) > max_level:
            continue
        if not item.tagged:
            if cautious or at_home or limits.max_intensity != 'high':
                continue
        else:
            if INTENSITY_ORDER.get(item.intensity, 99) > max_intensity:
                continue
            if set(item.risk_tags) & limits.excluded_tags:
                continue
            if at_home and not set(item.equipment) <= available:
                continue
        allowed.append(item)
    allowed.sort(key=lambda item: (item.category, item.name, item.id))
    return CatalogResult(allowed=tuple(allowed), limits=limits)
