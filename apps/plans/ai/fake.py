"""Cliente de IA SIMULADO (modo `simulated` y pruebas): sin red y sin costo.

Devuelve respuestas de ejemplo VÁLIDAS armadas con los ids del catálogo permitido que recibe
(así pasan la validación con cualquier catálogo). Los planes quedan `is_simulated=True` y no se
pueden aprobar fuera de DEBUG.

Guiones para las pruebas (`FakeAIClient(scenario=...)`): ok, invalid_json, wrong_count,
unknown_id, forbidden_text, allergen_text, kcal_out_of_band, timeout, rate_limited, permanent,
safety_block, max_tokens.
Un guion puede ser una lista: cada llamada consume el siguiente (el último se repite).
`calls` guarda lo que recibió cada llamada (para comprobar qué se envió).
"""
import random
from decimal import Decimal

from .base import (
    AIBlockedError, AIClient, AIInvalidOutput, AIPermanentError, AIResult, AITransientError, AIUsage,
)

FAKE_MODEL = 'simulado'
SCENARIOS = ('ok', 'invalid_json', 'wrong_count', 'unknown_id', 'forbidden_text', 'allergen_text',
             'kcal_out_of_band', 'timeout', 'rate_limited', 'permanent', 'safety_block', 'max_tokens')


def sample_exercise_plan(payload):
    """Plan válido construido solo con datos del payload (ids permitidos, días y topes)."""
    allowed = [item['id'] for item in payload['allowed_exercises']]
    limits = payload['limits']
    days = payload['training']['days_per_week']
    per_day = max(limits['exercises_per_day_min'], min(4, limits['exercises_per_day_max']))
    per_day = min(per_day, len(allowed))
    sets = min(3, limits['sets_max'])
    plan_days = []
    for index in range(days):
        start = (index * per_day) % len(allowed)
        chosen = [allowed[(start + offset) % len(allowed)] for offset in range(per_day)]
        plan_days.append({
            'name': f'Día {index + 1}: cuerpo completo',
            'exercises': [{'exercise_id': exercise_id, 'sets': sets, 'reps': '10-12',
                           'rest_seconds': 60, 'observations': 'Controla el movimiento.'}
                          for exercise_id in chosen],
        })
    return {
        'schema_version': 'exercise-plan/1',
        'routine_name': f'Plan de {days} días (borrador de prueba)',
        'general_notes': 'Calienta 5 minutos antes de cada sesión.',
        'days': plan_days,
    }


def _food_items(payload):
    from apps.plans.rules.foods import FoodItem
    return [
        FoodItem(id=row['id'], name=row['name'], group=row['group'], kcal100=Decimal(str(row['kcal100'])),
                 protein100=Decimal(str(row['p100'])), carbs100=Decimal(str(row['c100'])),
                 fat100=Decimal(str(row['f100'])), is_common=row.get('common', True))
        for row in payload['allowed_foods']
    ]


def _short(food):
    return food.name.split(' (')[0].split(' cocid')[0].split(' sin ')[0]


def sample_meal_slot(payload):
    """Opciones válidas construidas con los alimentos permitidos que recibe: busca combinaciones de
    una proteína, un carbohidrato, una verdura o fruta y a veces una grasa, y se queda con las que
    pasan las mismas reglas que la validación real (energía, proteína, grasa y variedad)."""
    from apps.plans.rules import meal_validate as m
    foods = _food_items(payload)
    slot = payload['slot']
    wanted = payload['options_requested']
    rules = m.SlotRules(slot_code=slot['code'], kcal=Decimal(str(slot['target_kcal'])),
                        protein_g=Decimal(str(slot['protein_g'])), foods={f.id: f for f in foods})
    def common_first(items):
        return [f for f in items if f.is_common] or items

    proteins = common_first([f for f in foods if f.protein100 >= 10])
    carbs = common_first([f for f in foods if f.carbs100 >= 15 and f.protein100 < 10])
    sides = common_first([f for f in foods if f.group in ('verduras', 'frutas')])
    fats = common_first([f for f in foods if f.group == 'grasas' and f.fat100 >= 50])
    rng = random.Random(payload['job_ref'])
    existing = payload.get('existing_options', [])
    avoid = {name.lower() for name in payload.get('avoid_repeating_names', [])}
    avoid |= {option['name'].lower() for option in existing}
    picked, used_sets = [], [frozenset(option['food_ids']) for option in existing]
    for _attempt in range(6000):
        if len(picked) == wanted:
            break
        if not (proteins and carbs and sides):
            break
        rows = [(rng.choice(proteins), rng.choice((60, 100, 150))), (rng.choice(carbs), rng.choice((60, 100, 150))),
                (rng.choice(sides), rng.choice((60, 100)))]
        if fats and rng.random() < 0.7:
            rows.append((rng.choice(fats), rng.choice((5, 10, 15))))
        if len({food.id for food, _grams in rows}) != len(rows):
            continue
        scaled, _total, errors = m.evaluate_option(rows, rules)
        ids = frozenset(food.id for food, _grams in rows)
        name = f'{_short(rows[0][0])} con {_short(rows[1][0]).lower()} y {_short(rows[2][0]).lower()}'
        if errors or name.lower() in avoid or any(m._jaccard(ids, other) >= m.VARIETY_MAX_OVERLAP for other in used_sets):
            continue
        used_sets.append(ids)
        avoid.add(name.lower())
        picked.append({'name': name, 'items': [{'food_id': f.id, 'grams': g} for f, g in rows],
                       'preparation': 'Prepara cada alimento sencillo, a la plancha o cocido, y sírvelos juntos.'})
    return {'schema_version': m.MEAL_SCHEMA_VERSION, 'slot_code': slot['code'], 'options': picked}


class FakeAIClient(AIClient):
    model_name = FAKE_MODEL
    is_simulated = True

    def __init__(self, scenario='ok'):
        self.scenarios = list(scenario) if isinstance(scenario, (list, tuple)) else [scenario]
        self.calls = []

    def _next_scenario(self):
        index = min(len(self.calls) - 1, len(self.scenarios) - 1)
        return self.scenarios[index]

    def generate_json(self, *, task, system, payload, schema, job_ref):
        self.calls.append({'task': task, 'system': system, 'payload': payload,
                           'schema': schema, 'job_ref': job_ref})
        scenario = self._next_scenario()
        usage = AIUsage(tokens_in=1000, tokens_out=500)
        if scenario == 'timeout':
            raise AITransientError('ai_timeout')
        if scenario == 'rate_limited':
            raise AITransientError('ai_http_429', retry_after=0)
        if scenario == 'permanent':
            raise AIPermanentError('ai_http_400')
        if scenario == 'safety_block':
            raise AIBlockedError('ai_blocked_safety')
        if scenario == 'invalid_json':
            raise AIInvalidOutput('ai_invalid_json')
        if task == 'meal_slot':
            data = self._meal_data(payload, scenario)
        else:
            data = sample_exercise_plan(payload)
            if scenario == 'wrong_count':
                data['days'] = data['days'][:-1] or []
            elif scenario == 'unknown_id':
                data['days'][0]['exercises'][0]['exercise_id'] = 987654321
            elif scenario == 'forbidden_text':
                data['general_notes'] = 'Toma un suplemento de proteína después de entrenar.'
        if scenario == 'max_tokens':
            return AIResult(data=data, usage=usage, model=FAKE_MODEL, finish_reason='MAX_TOKENS')
        return AIResult(data=data, usage=usage, model=FAKE_MODEL)

    @staticmethod
    def _meal_data(payload, scenario):
        data = sample_meal_slot(payload)
        options = data['options']
        if scenario == 'wrong_count' and options:
            data['options'] = options[:-1]
        elif scenario == 'unknown_id' and options:
            options[0]['items'][0]['food_id'] = 987654321
        elif scenario == 'forbidden_text' and options:
            options[0]['preparation'] = 'Acompáñalo con un suplemento después de comer.'
        elif scenario == 'allergen_text' and options:
            options[0]['name'] = 'Plato con salsa de maní y queso'
        elif scenario == 'kcal_out_of_band' and options:
            richest = max(payload['allowed_foods'], key=lambda row: row['kcal100'])
            other = next(row for row in payload['allowed_foods'] if row['id'] != richest['id'])
            options[0]['items'] = [{'food_id': richest['id'], 'grams': 600}, {'food_id': other['id'], 'grams': 5}]
        return data
