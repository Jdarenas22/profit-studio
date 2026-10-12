"""Validación de la salida de la IA (contrato, sección 3.6): nada sin validar llega a pantalla.

Módulo PURO (sin Django ni red). La IA devuelve JSON "sintácticamente válido" pero eso no
basta: aquí se comprueba el esquema exacto, que todo id esté entre los permitidos, los rangos y
los textos. Si algo falla se devuelven CÓDIGOS (`V01_SCHEMA:...`), nunca el contenido.

Fase B: plan de ejercicio (`exercise-plan/1`): V01, V02, V07 y V08.
"""
import re
import unicodedata
from dataclasses import dataclass

EXERCISE_SCHEMA_VERSION = 'exercise-plan/1'

V01_SCHEMA = 'V01_SCHEMA'
V02_IDS = 'V02_IDS'
V07_EXERCISE = 'V07_EXERCISE'
V08_TEXT = 'V08_TEXT'

# Longitudes (coinciden con los modelos: Routine.name 200, RoutineDay.name 100, reps 50)
MAX_ROUTINE_NAME = 150
MAX_GENERAL_NOTES = 600
MAX_DAY_NAME = 100
MAX_OBSERVATIONS = 200
MIN_EXERCISES_PER_DAY = 3
MAX_EXERCISES_PER_DAY = 8
MINUTES_PER_EXERCISE = 6
MIN_SETS = 1
MIN_REST, MAX_REST = 20, 180

REPS_RE = re.compile(r'^(\d{1,2}(-\d{1,2})?|\d{1,3} ?seg)$')
_TOP_KEYS = {'schema_version', 'routine_name', 'general_notes', 'days'}
_DAY_KEYS = {'name', 'exercises'}
_EXERCISE_KEYS = {'exercise_id', 'sets', 'reps', 'rest_seconds', 'observations'}

# Texto prohibido (sin tildes, en minúsculas). No es una lista de "malas palabras": evita que el
# plan recomiende productos o haga promesas médicas, que no le corresponden.
_FORBIDDEN_PATTERNS = [
    r'suplement', r'creatina', r'proteina en polvo', r'whey', r'esteroide', r'anabolic',
    r'quemador', r'termogenic', r'medicament', r'pastilla', r'farmaco', r'capsula',
    r'ayuno', r'detox', r'desintoxic', r'dieta', r'\bcura\b', r'\bcurar', r'garantiza',
    r'diagnostic', r'tratamiento medico', r'\bdosis\b',
]
_FORBIDDEN_RE = re.compile('|'.join(_FORBIDDEN_PATTERNS))
_URL_RE = re.compile(r'https?:|www\.|\b[a-z0-9-]+\.(com|co|net|org|io|app|ly)\b')
_EMAIL_RE = re.compile(r'\S+@\S+')
_PHONE_RE = re.compile(r'\+?\d[\d \-]{6,}\d')
_HTML_RE = re.compile(r'[<>]')
_CONTROL_RE = re.compile(r'[\x00-\x08\x0b-\x1f\x7f​-‏ -‮⁠-⁤﻿]')


@dataclass(frozen=True)
class ExerciseRules:
    allowed_ids: frozenset
    days: int                    # días pedidos (ficha: training_days_per_week)
    session_minutes: int
    max_sets: int = 6


@dataclass(frozen=True)
class ValidationResult:
    data: dict | None
    errors: tuple = ()           # ('V07_EXERCISE', 'detalle sin datos personales'), ...

    @property
    def ok(self):
        return not self.errors

    @property
    def codes(self):
        """Códigos únicos en orden ('V01_SCHEMA', ...)."""
        seen = []
        for code, _detail in self.errors:
            if code not in seen:
                seen.append(code)
        return tuple(seen)

    @property
    def error_code(self):
        return f'validation_failed:{self.codes[0]}' if self.errors else ''


def normalize_for_search(text):
    """Minúsculas y sin tildes (para buscar palabras prohibidas sin que una tilde las esconda)."""
    decomposed = unicodedata.normalize('NFKD', text or '')
    return ''.join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def clean_text(value):
    """NFC, sin caracteres de control/invisibles y con espacios colapsados."""
    text = unicodedata.normalize('NFC', value)
    text = _CONTROL_RE.sub('', text)
    return ' '.join(text.split())


def check_text(value, max_length, where, errors, required=False):
    """Valida un texto libre de la IA (V08). Devuelve el texto limpio o None si no es texto."""
    if not isinstance(value, str):
        errors.append((V01_SCHEMA, f'{where}: no es texto'))
        return None
    text = clean_text(value)
    if required and not text:
        errors.append((V08_TEXT, f'{where}: vacío'))
    if len(text) > max_length:
        errors.append((V08_TEXT, f'{where}: más de {max_length} caracteres'))
    if _HTML_RE.search(text):
        errors.append((V08_TEXT, f'{where}: contiene HTML'))
    plain = normalize_for_search(text)
    if _URL_RE.search(plain) or _EMAIL_RE.search(plain):
        errors.append((V08_TEXT, f'{where}: contiene un enlace o correo'))
    if any(sum(ch.isdigit() for ch in m.group()) >= 7 for m in _PHONE_RE.finditer(plain)):
        errors.append((V08_TEXT, f'{where}: contiene un número de teléfono'))
    if _FORBIDDEN_RE.search(plain):
        errors.append((V08_TEXT, f'{where}: recomendación no permitida'))
    return text


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _exact_keys(obj, keys, where, errors):
    if not isinstance(obj, dict):
        errors.append((V01_SCHEMA, f'{where}: no es un objeto'))
        return False
    if set(obj) != keys:
        errors.append((V01_SCHEMA, f'{where}: claves distintas a las esperadas'))
        return False
    return True


def validate_exercise_plan(data, rules):
    """Valida un `exercise-plan/1`. Devuelve `ValidationResult` con los datos limpios si todo
    está bien (los textos normalizados), o con los errores."""
    errors = []
    if not _exact_keys(data, _TOP_KEYS, 'plan', errors):
        return ValidationResult(None, tuple(errors))
    if data['schema_version'] != EXERCISE_SCHEMA_VERSION:
        errors.append((V01_SCHEMA, 'schema_version distinto'))
    cleaned = {
        'schema_version': EXERCISE_SCHEMA_VERSION,
        'routine_name': check_text(data['routine_name'], MAX_ROUTINE_NAME, 'routine_name', errors, required=True),
        'general_notes': check_text(data['general_notes'], MAX_GENERAL_NOTES, 'general_notes', errors),
        'days': [],
    }
    days = data['days']
    if not isinstance(days, list):
        errors.append((V01_SCHEMA, 'days: no es una lista'))
        return ValidationResult(None, tuple(errors))
    if len(days) != rules.days:
        errors.append((V07_EXERCISE, f'days: {len(days)} días, se pidieron {rules.days}'))
    max_per_day = max(MIN_EXERCISES_PER_DAY,
                      min(MAX_EXERCISES_PER_DAY, rules.session_minutes // MINUTES_PER_EXERCISE))

    for d_index, day in enumerate(days, start=1):
        where_day = f'día {d_index}'
        if not _exact_keys(day, _DAY_KEYS, where_day, errors):
            continue
        clean_day = {'name': check_text(day['name'], MAX_DAY_NAME, f'{where_day}.name', errors, required=True),
                     'exercises': []}
        exercises = day['exercises']
        if not isinstance(exercises, list):
            errors.append((V01_SCHEMA, f'{where_day}.exercises: no es una lista'))
            continue
        if not MIN_EXERCISES_PER_DAY <= len(exercises) <= max_per_day:
            errors.append((V07_EXERCISE, f'{where_day}: {len(exercises)} ejercicios '
                                         f'(permitido {MIN_EXERCISES_PER_DAY} a {max_per_day})'))
        seen_ids = set()
        for e_index, item in enumerate(exercises, start=1):
            where = f'{where_day}.ejercicio {e_index}'
            if not _exact_keys(item, _EXERCISE_KEYS, where, errors):
                continue
            exercise_id, sets, reps, rest = item['exercise_id'], item['sets'], item['reps'], item['rest_seconds']
            if not _is_int(exercise_id):
                errors.append((V01_SCHEMA, f'{where}.exercise_id: no es entero'))
            elif exercise_id not in rules.allowed_ids:
                errors.append((V02_IDS, f'{where}: ejercicio fuera del catálogo permitido'))
            elif exercise_id in seen_ids:
                errors.append((V07_EXERCISE, f'{where}: ejercicio repetido en el mismo día'))
            else:
                seen_ids.add(exercise_id)
            if not _is_int(sets):
                errors.append((V01_SCHEMA, f'{where}.sets: no es entero'))
            elif not MIN_SETS <= sets <= rules.max_sets:
                errors.append((V07_EXERCISE, f'{where}.sets fuera de {MIN_SETS} a {rules.max_sets}'))
            if not isinstance(reps, str):
                errors.append((V01_SCHEMA, f'{where}.reps: no es texto'))
                reps = ''
            else:
                reps = clean_text(reps)
                if not REPS_RE.match(reps):
                    errors.append((V07_EXERCISE, f'{where}.reps con formato no permitido'))
            if not _is_int(rest):
                errors.append((V01_SCHEMA, f'{where}.rest_seconds: no es entero'))
            elif not MIN_REST <= rest <= MAX_REST:
                errors.append((V07_EXERCISE, f'{where}.rest_seconds fuera de {MIN_REST} a {MAX_REST}'))
            observations = check_text(item['observations'], MAX_OBSERVATIONS, f'{where}.observations', errors)
            clean_day['exercises'].append({
                'exercise_id': exercise_id, 'sets': sets, 'reps': reps,
                'rest_seconds': rest, 'observations': observations or '',
            })
        cleaned['days'].append(clean_day)

    if errors:
        return ValidationResult(None, tuple(errors))
    return ValidationResult(cleaned, ())
