"""Formulario de la ficha de salud y alimentación (validación en el SERVIDOR).

Un solo `HealthProfileForm` recibe los cinco pasos del asistente (la clienta y el entrenador
usan el mismo). `SECTION_FIELDS` dice qué campos pertenecen a cada paso; `error_steps(errors)`
devuelve en qué pasos hay errores para llevar a la persona al primero.

Reglas (docs/contracts/plan-ia.md, 2.2): listas cerradas contra `choices.py`; rangos numéricos;
coherencias entre campos; textos libres limpios (NFC, sin caracteres de control, sin `<` ni `>`,
espacios colapsados, máximo 300 caracteres). Mayores de 18 y hasta 100 años.

Convención de `form` para las plantillas (`form_values_*`): un diccionario donde los campos de
lista (`conditions`, `medications`, `injuries`, `allergies`, `intolerances`, `meal_slots`,
`equipment`) son SIEMPRE listas (para usar `{% if 'knee' in form.injuries %}checked{% endif %}`),
las casillas únicas (`condition_controlled`, `medical_clearance`) son true/false, y el resto son
cadenas de texto (vacías si no vienen). Los 7 cuestionarios van como `parq_q1`..`parq_q7`
('yes' / 'no' / '') y los horarios como `meal_time_<comida>` ('HH:MM' / '').
"""
import re
import unicodedata
from datetime import date
from decimal import Decimal

from django import forms

from apps.accounts.form_utils import DecimalInRangeField
from apps.accounts.models import User
from apps.assessments.forms import IMC_MAX, IMC_MIN

from . import choices as ch
from . import dates

AGE_MAX = 100
TARGET_WEIGHT_MIN, TARGET_WEIGHT_MAX = Decimal('30'), Decimal('250')
SLEEP_MIN, SLEEP_MAX = Decimal('3'), Decimal('12')
TRAINING_DAYS_MIN, TRAINING_DAYS_MAX = 1, 6
SESSION_MIN, SESSION_MAX = 20, 120
EATS_OUT_MIN, EATS_OUT_MAX = 0, 21
TEXT_MAX = 300
RAW_TEXT_MAX = 2000                 # tope antes de limpiar (evita trabajo con textos enormes)
CLEARANCE_EARLIEST = date(1990, 1, 1)

LIST_FIELDS = ('conditions', 'medications', 'injuries', 'allergies', 'intolerances',
               'meal_slots', 'equipment')
BOOLEAN_FIELDS = ('condition_controlled', 'medical_clearance')
TEXT_FIELDS = ('other_condition_text', 'medications_text', 'injuries_text',
               'disliked_foods_text', 'liked_foods_text')
PARQ_FIELDS = tuple(f'parq_{key}' for key in ch.PARQ_KEYS)
MEAL_TIME_FIELDS = tuple(f'meal_time_{code}' for code in ch.codes(ch.MEAL_SLOT_CHOICES))

SECTION_FIELDS = {
    1: ('birth_date', 'sex_for_calculation', 'training_goal', 'target_weight_kg'),
    2: ('conditions', 'condition_controlled', 'medical_clearance', 'clearance_date', 'medications',
        'injuries', 'recent_surgery', 'pregnancy_status', 'eating_disorder_history',
        'other_condition_text', 'medications_text', 'injuries_text'),
    3: PARQ_FIELDS,
    4: ('diet_type', 'allergies', 'allergy_severity', 'intolerances', 'meal_slots',
        *MEAL_TIME_FIELDS, 'cooking_access', 'budget_level', 'eats_out_per_week',
        'disliked_foods_text', 'liked_foods_text'),
    5: ('training_days_per_week', 'session_minutes', 'training_place', 'equipment',
        'experience_level', 'activity_level', 'sleep_hours'),
}
FIELD_STEP = {name: step for step, names in SECTION_FIELDS.items() for name in names}

_HTML_CHARS = re.compile(r'[<>]')
_TIME_RE = re.compile(r'^([01]?\d|2[0-3]):([0-5]\d)$')

REQUIRED = 'Este dato es obligatorio.'
CHOICE_INVALID = 'Elige una opción de la lista.'


def error_steps(errors):
    """Pasos (1-5) que tienen algún error, de menor a mayor. Los errores generales (`__all__`)
    cuentan en el paso 1."""
    steps = {FIELD_STEP.get(name, 1) for name in errors}
    return sorted(steps)


def first_error_step(errors):
    steps = error_steps(errors)
    return steps[0] if steps else None


# ─── Campos ───────────────────────────────────────────────────────────────────

class CleanTextField(forms.CharField):
    """Texto libre corto: NFC, sin caracteres de control ni de formato, sin `<` ni `>`,
    espacios colapsados y máximo 300 caracteres (contados sobre el texto ya limpio).
    Se guarda tal cual limpio y se muestra siempre escapado."""

    def __init__(self, *, max_chars=TEXT_MAX, **kwargs):
        self.max_chars = max_chars
        kwargs.setdefault('required', False)
        super().__init__(strip=True, **kwargs)

    def to_python(self, value):
        value = super().to_python(value)
        if not value:
            return ''
        if len(value) > RAW_TEXT_MAX:
            raise forms.ValidationError(f'Máximo {self.max_chars} caracteres.')
        value = unicodedata.normalize('NFC', value)
        value = ''.join(' ' if ch_.isspace() else ch_ for ch_ in value
                        if ch_.isspace() or unicodedata.category(ch_) not in ('Cc', 'Cf', 'Co', 'Cs', 'Cn'))
        value = re.sub(r' {2,}', ' ', value).strip()
        if _HTML_CHARS.search(value):
            raise forms.ValidationError('No uses los símbolos < ni >.')
        if len(value) > self.max_chars:
            raise forms.ValidationError(f'Máximo {self.max_chars} caracteres.')
        return value


def _choice(choices, required=True, label=''):
    return forms.ChoiceField(
        choices=choices, required=required, label=label,
        error_messages={'required': REQUIRED, 'invalid_choice': CHOICE_INVALID},
    )


def _multi(choices, label=''):
    return forms.MultipleChoiceField(
        choices=choices, required=False, label=label,
        error_messages={'invalid_choice': CHOICE_INVALID, 'invalid_list': CHOICE_INVALID},
    )


def _int(min_value, max_value, required=True, label=''):
    return forms.IntegerField(
        min_value=min_value, max_value=max_value, required=required, label=label,
        error_messages={'required': REQUIRED, 'invalid': f'Escribe un número entero entre {min_value} y {max_value}.',
                        'min_value': f'Debe estar entre {min_value} y {max_value}.',
                        'max_value': f'Debe estar entre {min_value} y {max_value}.'},
    )


def _ordered(selected, choices):
    """Sin repetidos y en el orden de la lista cerrada."""
    chosen = set(selected or [])
    return [code for code, _ in choices if code in chosen]


class HealthProfileForm(forms.Form):
    # Paso 1 — datos básicos y meta
    birth_date = forms.DateField(
        label='Fecha de nacimiento', input_formats=['%Y-%m-%d', '%d/%m/%Y'],
        error_messages={'required': 'Escribe tu fecha de nacimiento.',
                        'invalid': 'Fecha no válida. Usa día/mes/año (por ejemplo 25/03/1990).'},
    )
    sex_for_calculation = _choice(ch.SEX_CHOICES, label='Sexo para el cálculo')
    training_goal = _choice(User.GOAL_CHOICES, label='Meta')
    target_weight_kg = DecimalInRangeField(
        label='Peso meta (kg)', required=False, places=1,
        min_value=TARGET_WEIGHT_MIN, max_value=TARGET_WEIGHT_MAX,
        error_messages={'invalid': 'Peso meta inválido.',
                        'min_value': f'El peso meta debe estar entre {TARGET_WEIGHT_MIN} y {TARGET_WEIGHT_MAX} kg.',
                        'max_value': f'El peso meta debe estar entre {TARGET_WEIGHT_MIN} y {TARGET_WEIGHT_MAX} kg.'},
    )

    # Paso 2 — salud
    conditions = _multi(ch.CONDITION_CHOICES, 'Condiciones')
    condition_controlled = forms.BooleanField(required=False, label='Condiciones controladas')
    medical_clearance = forms.BooleanField(required=False, label='Autorización médica')
    clearance_date = forms.DateField(
        label='Fecha de la autorización médica', required=False, input_formats=['%Y-%m-%d', '%d/%m/%Y'],
        error_messages={'invalid': 'Fecha no válida. Usa día/mes/año.'},
    )
    medications = _multi(ch.MEDICATION_CHOICES, 'Medicamentos')
    injuries = _multi(ch.INJURY_CHOICES, 'Lesiones')
    recent_surgery = _choice(ch.SURGERY_CHOICES, label='Cirugía reciente')
    pregnancy_status = _choice(ch.PREGNANCY_CHOICES, label='Embarazo o lactancia')
    eating_disorder_history = _choice(ch.EATING_DISORDER_CHOICES, label='Antecedentes alimentarios')
    other_condition_text = CleanTextField(label='Otra condición')
    medications_text = CleanTextField(label='Medicamentos')
    injuries_text = CleanTextField(label='Lesiones')

    # Paso 4 — alimentación
    diet_type = _choice(ch.DIET_CHOICES, label='Tipo de dieta')
    allergies = _multi(ch.ALLERGY_CHOICES, 'Alergias')
    allergy_severity = _choice(ch.ALLERGY_SEVERITY_CHOICES, required=False, label='Gravedad de la alergia')
    intolerances = _multi(ch.INTOLERANCE_CHOICES, 'Intolerancias')
    meal_slots = _multi(ch.MEAL_SLOT_CHOICES, 'Comidas del día')
    cooking_access = _choice(ch.COOKING_CHOICES, label='Cocina')
    budget_level = _choice(ch.BUDGET_CHOICES, label='Presupuesto')
    eats_out_per_week = _int(EATS_OUT_MIN, EATS_OUT_MAX, required=False, label='Comidas fuera por semana')
    disliked_foods_text = CleanTextField(label='Alimentos que no te gustan')
    liked_foods_text = CleanTextField(label='Alimentos que te gustan')

    # Paso 5 — entrenamiento
    training_days_per_week = _int(TRAINING_DAYS_MIN, TRAINING_DAYS_MAX, label='Días por semana')
    session_minutes = _int(SESSION_MIN, SESSION_MAX, label='Minutos por sesión')
    training_place = _choice(ch.TRAINING_PLACE_CHOICES, label='Lugar')
    equipment = _multi(ch.EQUIPMENT_CHOICES, 'Equipo')
    experience_level = _choice(ch.EXPERIENCE_CHOICES, label='Experiencia')
    activity_level = _choice(ch.ACTIVITY_CHOICES, label='Actividad')
    sleep_hours = DecimalInRangeField(
        label='Horas de sueño', required=False, places=1, min_value=SLEEP_MIN, max_value=SLEEP_MAX,
        error_messages={'invalid': 'Horas de sueño inválidas.',
                        'min_value': f'Las horas de sueño deben estar entre {SLEEP_MIN} y {SLEEP_MAX}.',
                        'max_value': f'Las horas de sueño deben estar entre {SLEEP_MIN} y {SLEEP_MAX}.'},
    )

    def __init__(self, data=None, *, known_height=None, **kwargs):
        super().__init__(data, **kwargs)
        self.known_height = known_height
        # Paso 3: siete respuestas Sí/No
        for key in ch.PARQ_KEYS:
            self.fields[f'parq_{key}'] = forms.ChoiceField(
                choices=[('yes', 'Sí'), ('no', 'No')], required=True, label=key,
                error_messages={'required': 'Responde Sí o No.', 'invalid_choice': 'Responde Sí o No.'},
            )
        # Paso 4: hora de cada comida (opcional, HH:MM)
        for code, label in ch.MEAL_SLOT_CHOICES:
            self.fields[f'meal_time_{code}'] = forms.CharField(
                required=False, label=f'Hora de {label.lower()}', max_length=10,
                error_messages={'max_length': 'Hora no válida. Usa HH:MM.'},
            )

    # ─── Validación de campos sueltos ─────────────────────────────────────────
    def clean_birth_date(self):
        born = self.cleaned_data['birth_date']
        age = dates.age_today(born)
        if born > dates.today() or age < 0:
            raise forms.ValidationError('La fecha de nacimiento no puede ser futura.')
        if age < 18:
            raise forms.ValidationError(
                'La ficha en línea es solo para mayores de 18 años. Consulta con tu entrenador.')
        if age > AGE_MAX:
            raise forms.ValidationError('Revisa la fecha de nacimiento: la edad debe ser de 100 años o menos.')
        return born

    def clean_clearance_date(self):
        day = self.cleaned_data.get('clearance_date')
        if day is None:
            return None
        if day > dates.today():
            raise forms.ValidationError('La fecha de la autorización no puede ser futura.')
        if day < CLEARANCE_EARLIEST:
            raise forms.ValidationError('Revisa la fecha de la autorización médica.')
        return day

    # ─── Coherencias entre campos ─────────────────────────────────────────────
    def clean(self):
        data = super().clean()
        err = self.add_error

        # Listas: sin repetidos y en orden canónico
        for name, choices in (('conditions', ch.CONDITION_CHOICES), ('medications', ch.MEDICATION_CHOICES),
                              ('injuries', ch.INJURY_CHOICES), ('allergies', ch.ALLERGY_CHOICES),
                              ('intolerances', ch.INTOLERANCE_CHOICES)):
            if name in data:
                data[name] = _ordered(data[name], choices)

        # Cuestionario de aptitud -> {q1: bool, ...}
        parq = {}
        for key in ch.PARQ_KEYS:
            answer = data.pop(f'parq_{key}', None)
            if answer is not None:
                parq[key] = answer == 'yes'
        if len(parq) == len(ch.PARQ_KEYS):
            data['parq'] = parq

        # Embarazo / lactancia / posparto solo con sexo femenino o sin especificar
        if data.get('pregnancy_status', 'none') != 'none' and data.get('sex_for_calculation') == 'M':
            err('pregnancy_status', 'Esta opción no coincide con el sexo indicado. Revisa tus respuestas.')

        # Autorización médica: exige fecha; sin autorización se limpia la fecha
        if 'medical_clearance' in data:
            if data['medical_clearance']:
                if data.get('clearance_date') is None and 'clearance_date' not in self.errors:
                    err('clearance_date', 'Escribe la fecha de la autorización de tu médico.')
            else:
                data['clearance_date'] = None

        # Sin condiciones declaradas no hay "condiciones controladas"
        if 'conditions' in data and not data['conditions']:
            data['condition_controlled'] = False

        # Alergias: la gravedad solo aplica si hay alergias
        if 'allergies' in data:
            if data['allergies']:
                if not data.get('allergy_severity') and 'allergy_severity' not in self.errors:
                    err('allergy_severity', 'Indica qué tan fuerte es tu alergia.')
            else:
                data['allergy_severity'] = ''

        # Comidas: siempre desayuno, almuerzo y cena; opcionales media mañana y merienda (3 a 5)
        if 'meal_slots' in data:
            slots = _ordered(data['meal_slots'], ch.MEAL_SLOT_CHOICES)
            if any(code not in slots for code in ch.REQUIRED_MEAL_SLOTS):
                err('meal_slots', 'Incluye siempre desayuno, almuerzo y cena. '
                                  'Puedes agregar media mañana y merienda.')
            data['meal_slots'] = slots

        # Horarios: HH:MM y solo de las comidas elegidas
        meal_times = {}
        for code in ch.codes(ch.MEAL_SLOT_CHOICES):
            raw = (data.pop(f'meal_time_{code}', '') or '').strip()
            if not raw:
                continue
            match = _TIME_RE.match(raw)
            if not match:
                err(f'meal_time_{code}', 'Hora no válida. Usa HH:MM (por ejemplo 07:30).')
            elif code in data.get('meal_slots', []):
                meal_times[code] = f'{int(match.group(1)):02d}:{match.group(2)}'
        data['meal_times'] = meal_times

        if data.get('eats_out_per_week') is None:
            data['eats_out_per_week'] = 0

        # Equipo: obligatorio en casa ("ninguno" es exclusivo); en el gimnasio no aplica
        if 'training_place' in data and 'equipment' in data:
            equipment = _ordered(data['equipment'], ch.EQUIPMENT_CHOICES)
            if data['training_place'] == 'home':
                if not equipment:
                    err('equipment', 'Indica con qué equipo cuentas en casa (o "Ninguno").')
                elif 'none' in equipment and len(equipment) > 1:
                    err('equipment', '"Ninguno" no se puede combinar con otro equipo.')
            else:
                equipment = []
            data['equipment'] = equipment

        # Peso meta coherente con la estatura conocida
        target, height = data.get('target_weight_kg'), self.known_height
        if target is not None and height:
            imc = float(target) / (float(height) ** 2)
            if not IMC_MIN <= imc <= IMC_MAX:
                err('target_weight_kg', 'El peso meta no es coherente con tu estatura. Revísalo.')

        return data


# ─── Valores para repoblar la plantilla (`form`) ──────────────────────────────

def _text(value):
    return '' if value is None else str(value)


def _date_text(value):
    return value.isoformat() if hasattr(value, 'isoformat') else _text(value)


def form_values_from_post(post):
    """Valores crudos del POST, con la forma estable que usan las plantillas (ver arriba)."""
    values = {}
    for name in HealthProfileForm.base_fields:
        if name in LIST_FIELDS:
            values[name] = list(post.getlist(name)) if hasattr(post, 'getlist') else list(post.get(name, []))
        elif name in BOOLEAN_FIELDS:
            values[name] = _text(post.get(name)).lower() in ('on', 'true', '1', 'yes')
        else:
            values[name] = _text(post.get(name))
    for name in (*PARQ_FIELDS, *MEAL_TIME_FIELDS):
        values[name] = _text(post.get(name))
    return values


def form_values_from_profile(profile, training_goal=''):
    """Valores de una ficha existente (para editarla), en la misma forma que `form_values_from_post`."""
    values = {}
    for name in HealthProfileForm.base_fields:
        if name == 'training_goal':
            values[name] = training_goal or ''
        elif name in LIST_FIELDS:
            values[name] = list(getattr(profile, name) or [])
        elif name in BOOLEAN_FIELDS:
            values[name] = bool(getattr(profile, name))
        elif name in ('birth_date', 'clearance_date'):
            values[name] = _date_text(getattr(profile, name))
        else:
            values[name] = _text(getattr(profile, name))
    answers = profile.parq or {}
    for key in ch.PARQ_KEYS:
        values[f'parq_{key}'] = 'yes' if answers.get(key) else ('no' if key in answers else '')
    times = profile.meal_times or {}
    for code in ch.codes(ch.MEAL_SLOT_CHOICES):
        values[f'meal_time_{code}'] = times.get(code, '')
    return values


def empty_form_values():
    """Valores vacíos con la forma estable (primer GET)."""
    values = {}
    for name in HealthProfileForm.base_fields:
        values[name] = [] if name in LIST_FIELDS else (False if name in BOOLEAN_FIELDS else '')
    for name in (*PARQ_FIELDS, *MEAL_TIME_FIELDS):
        values[name] = ''
    # Casillas que arrancan marcadas: desayuno, almuerzo y cena
    values['meal_slots'] = list(ch.REQUIRED_MEAL_SLOTS)
    return values
