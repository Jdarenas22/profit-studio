"""Ficha básica: las dos preguntas que despliegan y los valores por defecto (sin Django).

Referencia: docs/contracts/ficha-basica.md (secciones 3, 4 y 5). Lo usan el formulario
(`forms.py`), el servicio (`services.py`, última defensa) y el modelo (propiedades de lectura),
para que la regla sea UNA sola.

- `health_screen` / `food_screen`: `yes` o `no` en cada guardado nuevo. `legacy` solo existe en
  las fichas anteriores a la ficha básica (respondieron el cuestionario completo; clínicamente
  valen lo mismo que `yes`).
- Con `no`, lo médico (o lo alimentario) se reemplaza por los valores neutros de abajo: lo que
  llegue en el POST nunca se guarda.
- `defaulted_fields`: datos opcionales que la clienta no declaró y que pueden cambiar un plan; se
  guardan con un valor por defecto prudente y quedan marcados como «no declarado».

[A VALIDAR por un profesional de la salud] que la pregunta general de salud sustituya al
cuestionario de aptitud visible (decisión 1A del contrato).
"""
from . import choices as ch

SCREEN_YES = 'yes'
SCREEN_NO = 'no'
SCREEN_LEGACY = 'legacy'
# Lo que se acepta en un guardado nuevo (legacy no)
SCREEN_ANSWERS = (SCREEN_YES, SCREEN_NO)

HEALTH_SCREEN_REQUIRED = 'Cuéntanos si tienes algún problema de salud (Sí o No).'
FOOD_SCREEN_REQUIRED = 'Cuéntanos si tienes alergias, intolerancias o una dieta especial (Sí o No).'
HEALTH_YES_EMPTY = 'Marcaste que sí, pero no indicaste qué. Márcalo abajo o cambia tu respuesta a No.'
FOOD_YES_EMPTY = 'Marcaste que sí, pero no indicaste cuál. Márcalo o cambia tu respuesta a No.'

# Campos de la ficha (modelo) que dependen de cada pregunta
HEALTH_MODEL_FIELDS = (
    'parq', 'conditions', 'medications', 'injuries', 'condition_controlled', 'medical_clearance',
    'clearance_date', 'recent_surgery', 'pregnancy_status', 'eating_disorder_history',
    'other_condition_text', 'medications_text', 'injuries_text',
)
FOOD_MODEL_FIELDS = ('diet_type', 'allergies', 'allergy_severity', 'intolerances')


def health_neutral_values():
    """Valores que se guardan con `health_screen == 'no'` (copias nuevas en cada llamada)."""
    return {
        'parq': {key: False for key in ch.PARQ_KEYS},
        'conditions': [], 'medications': [], 'injuries': [],
        'condition_controlled': False, 'medical_clearance': False, 'clearance_date': None,
        'recent_surgery': 'none', 'pregnancy_status': 'none', 'eating_disorder_history': 'no',
        'other_condition_text': '', 'medications_text': '', 'injuries_text': '',
    }


def food_neutral_values():
    """Valores que se guardan con `food_screen == 'no'`."""
    return {'diet_type': 'omnivore', 'allergies': [], 'allergy_severity': '', 'intolerances': []}


def _value(source, name):
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def health_is_neutral(source):
    """¿Lo médico de `source` (diccionario o ficha) es todo neutro? Un campo ausente cuenta como
    neutro. Neutro = 7 «No», listas vacías, sin cirugía ni embarazo, antecedentes `no`, sin
    casillas marcadas y sin textos."""
    parq = _value(source, 'parq') or {}
    if any(bool(parq.get(key)) for key in ch.PARQ_KEYS):
        return False
    for name in ('conditions', 'medications', 'injuries', 'condition_controlled', 'medical_clearance',
                 'other_condition_text', 'medications_text', 'injuries_text'):
        if _value(source, name):
            return False
    return (_value(source, 'recent_surgery') in (None, '', 'none')
            and _value(source, 'pregnancy_status') in (None, '', 'none')
            and _value(source, 'eating_disorder_history') in (None, '', 'no'))


def food_is_neutral(source):
    """¿Lo alimentario es neutro? (come de todo, sin alergias ni intolerancias)."""
    return (_value(source, 'diet_type') in (None, '', 'omnivore')
            and not _value(source, 'allergies') and not _value(source, 'intolerances'))


def legacy_screen_guess(profile):
    """Preselección al editar una ficha: la respuesta guardada, o, en una ficha `legacy`, `yes` si
    hay algún dato distinto del neutro y `no` si todo es neutro (contrato, sección 5)."""
    health = _value(profile, 'health_screen')
    food = _value(profile, 'food_screen')
    if health not in SCREEN_ANSWERS:
        health = SCREEN_NO if health_is_neutral(profile) else SCREEN_YES
    if food not in SCREEN_ANSWERS:
        food = SCREEN_NO if food_is_neutral(profile) else SCREEN_YES
    return health, food


# ─── «Más detalles (opcional)»: valores por defecto ───────────────────────────
# Solo los que pueden cambiar un plan quedan en `defaulted_fields` (contrato, 4.3). El orden de
# esta tupla es el orden canónico de la lista guardada.
DEFAULTABLE_FIELDS = ('cooking_access', 'budget_level', 'eats_out_per_week', 'equipment', 'activity_level')
DEFAULTABLE_LABELS = {
    'cooking_access': 'Posibilidad de cocinar',
    'budget_level': 'Presupuesto',
    'eats_out_per_week': 'Comidas fuera por semana',
    'equipment': 'Equipo en casa',
    'activity_level': 'Nivel de actividad diaria',
}
DEFAULT_VALUES = {
    'cooking_access': 'basic',
    'budget_level': 'medium',
    'eats_out_per_week': 0,
    'equipment': ['none'],          # solo cuando entrena en casa
    'activity_level': 'sedentary',  # prudente para el ejercicio; la alimentación se bloquea (D07)
}
DEFAULT_MEAL_SLOTS = list(ch.REQUIRED_MEAL_SLOTS)


def ordered_defaulted(names):
    """Sin repetidos y en el orden canónico."""
    chosen = set(names or ())
    return [name for name in DEFAULTABLE_FIELDS if name in chosen]


def defaulted_labels(names):
    return [DEFAULTABLE_LABELS.get(name, name) for name in ordered_defaulted(names)]
