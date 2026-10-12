"""Prueba la conexión con Google Gemini con datos FICTICIOS (ninguna clienta real).

Úsalo después de configurar `GEMINI_API_KEY` y `GEMINI_MODEL` (y antes de `AI_PLANS_MODE=live`):
confirma que la clave, el modelo y el formato de salida funcionan, y cuánto costó en tokens.

    python manage.py check_gemini

No imprime la clave ni la respuesta: solo si el formato fue válido, los tokens y la latencia.
Si falla con `ai_http_400`, prueba `GEMINI_SCHEMA_FIELD=none` (Google cambia los nombres de
campos) y revisa el nombre del modelo en la documentación vigente.
"""
import time
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.plans.ai import AIError
from apps.plans.ai.gemini import GeminiClient
from apps.plans.ai.prompts import EXERCISE_SYSTEM, MEAL_SYSTEM, exercise_schema, meal_schema
from apps.plans.foods_data import food_rows
from apps.plans.rules import foods as rules_foods
from apps.plans.rules import meal_validate, validate

SAMPLE_EXERCISES = [
    (1, 'Sentadilla con peso corporal', 'Piernas'), (2, 'Zancadas', 'Piernas'),
    (3, 'Puente de glúteo', 'Glúteos'), (4, 'Flexiones de brazos inclinadas', 'Pecho'),
    (5, 'Remo con banda elástica', 'Espalda'), (6, 'Plancha', 'Abdomen'),
    (7, 'Elevaciones laterales con mancuernas', 'Hombros'), (8, 'Peso muerto rumano con mancuernas', 'Piernas'),
]


def sample_payload():
    return {
        'task': 'exercise_plan', 'schema_version': validate.EXERCISE_SCHEMA_VERSION,
        'job_ref': str(uuid.uuid4()),
        'client': {'age': 30, 'sex': 'F', 'goal': 'toning', 'experience': 'beginner',
                   'activity': 'light', 'conservative': False},
        'training': {'days_per_week': 2, 'session_minutes': 45, 'place': 'gym'},
        'limits': {'exercises_per_day_min': 3, 'exercises_per_day_max': 5, 'sets_max': 4,
                   'rest_seconds_min': validate.MIN_REST, 'rest_seconds_max': validate.MAX_REST,
                   'intensity_max': 'high'},
        'allowed_exercises': [{'id': pk, 'name': name, 'category': cat, 'level': 'beginner',
                               'intensity': 'moderate'} for pk, name, cat in SAMPLE_EXERCISES],
        'trainer_notes': None, 'previous_errors': [],
    }


def sample_meal_payload(foods):
    return {
        'task': 'meal_slot', 'schema_version': meal_validate.MEAL_SCHEMA_VERSION, 'job_ref': str(uuid.uuid4()),
        'slot': {'code': 'lunch', 'target_kcal': 530, 'protein_g': 38, 'carbs_g': 58, 'fat_g': 16},
        'options_requested': 3,
        'client': {'age': 30, 'sex': 'F', 'goal': 'toning', 'diet': 'omnivore', 'cooking': 'basic',
                   'budget': 'medium', 'conservative': False},
        'allowed_foods': [{'id': f.id, 'name': f.name, 'group': f.group, 'kcal100': float(f.kcal100),
                           'p100': float(f.protein100), 'c100': float(f.carbs100), 'f100': float(f.fat100),
                           'common': f.is_common} for f in foods],
        'existing_options': [], 'avoid_repeating_names': [], 'trainer_notes': None, 'previous_errors': [],
    }


def sample_foods():
    return [rules_foods.FoodItem(
        id=index + 1, name=row['name'], group=row['group'], kcal100=row['kcal100'], protein100=row['protein100'],
        carbs100=row['carbs100'], fat100=row['fat100'], is_common=row['is_common'],
    ) for index, row in enumerate(food_rows())]


class Command(BaseCommand):
    help = 'Prueba Google Gemini con datos ficticios (clave, modelo y formato de salida).'
    requires_system_checks = []   # no toca la base: se puede correr en un equipo sin acceso a ella

    def handle(self, *args, **options):
        if not settings.GEMINI_API_KEY or not settings.GEMINI_MODEL:
            raise CommandError('Faltan GEMINI_API_KEY o GEMINI_MODEL.')
        if not settings.GEMINI_BILLING_CONFIRMED:
            self.stdout.write(self.style.WARNING(
                'Aviso: GEMINI_BILLING_CONFIRMED no está en true. Con datos reales la clave debe ser de un '
                'proyecto con facturación (esta prueba solo usa datos ficticios).'))
        client = GeminiClient(api_key=settings.GEMINI_API_KEY, model=settings.GEMINI_MODEL,
                              timeout=settings.GEMINI_TIMEOUT_SECONDS, schema_field=settings.GEMINI_SCHEMA_FIELD)
        payload = sample_payload()
        rules = validate.ExerciseRules(allowed_ids=frozenset(pk for pk, _n, _c in SAMPLE_EXERCISES),
                                       days=2, session_minutes=45, max_sets=4)
        started = time.monotonic()
        try:
            result = client.generate_json(
                task='exercise_plan', system=EXERCISE_SYSTEM, payload=payload,
                schema=exercise_schema(rules.allowed_ids, 2, 5, 4), job_ref=payload['job_ref'])
        except AIError as exc:
            raise CommandError(f'Gemini respondió con error: {exc.code}') from None
        latency = int((time.monotonic() - started) * 1000)
        self.stdout.write(f'Modelo: {result.model} | fin: {result.finish_reason} | tokens entrada/salida: '
                          f'{result.usage.tokens_in}/{result.usage.tokens_out} | {latency} ms')
        if result.finish_reason != 'STOP':
            raise CommandError('La respuesta no terminó normalmente (sube el límite o cambia de modelo).')
        check = validate.validate_exercise_plan(result.data, rules)
        if not check.ok:
            raise CommandError('La respuesta no pasó la validación: ' + ', '.join(check.codes))
        self.stdout.write(self.style.SUCCESS('Plan de ejercicio: Gemini responde y el formato es válido.'))
        self.check_meal(client)

    def check_meal(self, client):
        """Segunda prueba: una comida del plan de alimentación (esquema con lista de ids de alimentos)."""
        foods = sample_foods()
        payload = sample_meal_payload(foods)
        rules = meal_validate.SlotRules(
            slot_code='lunch', kcal=meal_validate.D(530), protein_g=meal_validate.D(38),
            foods={f.id: f for f in foods})
        started = time.monotonic()
        try:
            result = client.generate_json(
                task='meal_slot', system=MEAL_SYSTEM, payload=payload,
                schema=meal_schema(rules.foods.keys(), 'lunch', 3), job_ref=payload['job_ref'])
        except AIError as exc:
            raise CommandError(f'Gemini respondió con error en la comida: {exc.code}') from None
        latency = int((time.monotonic() - started) * 1000)
        self.stdout.write(f'Comida | modelo: {result.model} | fin: {result.finish_reason} | tokens entrada/salida: '
                          f'{result.usage.tokens_in}/{result.usage.tokens_out} | {latency} ms')
        if result.finish_reason != 'STOP':
            raise CommandError('La respuesta de la comida no terminó normalmente.')
        check = meal_validate.validate_meal_slot(result.data, rules)
        if check.ok:
            self.stdout.write(self.style.SUCCESS('Plan de alimentación: el formato y las reglas se cumplen.'))
        elif set(check.codes) & {validate.V01_SCHEMA, validate.V02_IDS}:
            raise CommandError('La comida no tiene el formato esperado: ' + ', '.join(check.codes))
        else:
            self.stdout.write(self.style.WARNING(
                'El formato es válido, pero la propuesta no cumplió las reglas de nutrición ('
                + ', '.join(check.codes) + '). En uso real se reintenta una vez; si ocurre seguido, prueba otro modelo.'))
