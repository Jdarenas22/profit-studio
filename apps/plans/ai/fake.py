"""Cliente de IA SIMULADO (modo `simulated` y pruebas): sin red y sin costo.

Devuelve respuestas de ejemplo VÁLIDAS armadas con los ids del catálogo permitido que recibe
(así pasan la validación con cualquier catálogo). Los planes quedan `is_simulated=True` y no se
pueden aprobar fuera de DEBUG.

Guiones para las pruebas (`FakeAIClient(scenario=...)`): ok, invalid_json, wrong_count,
unknown_id, forbidden_text, timeout, rate_limited, permanent, safety_block, max_tokens.
Un guion puede ser una lista: cada llamada consume el siguiente (el último se repite).
`calls` guarda lo que recibió cada llamada (para comprobar qué se envió).
"""
from .base import (
    AIBlockedError, AIClient, AIInvalidOutput, AIPermanentError, AIResult, AITransientError, AIUsage,
)

FAKE_MODEL = 'simulado'
SCENARIOS = ('ok', 'invalid_json', 'wrong_count', 'unknown_id', 'forbidden_text', 'timeout',
             'rate_limited', 'permanent', 'safety_block', 'max_tokens')


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
        data = sample_exercise_plan(payload)
        if scenario == 'max_tokens':
            return AIResult(data=data, usage=usage, model=FAKE_MODEL, finish_reason='MAX_TOKENS')
        if scenario == 'wrong_count':
            data['days'] = data['days'][:-1] or []
        elif scenario == 'unknown_id':
            data['days'][0]['exercises'][0]['exercise_id'] = 987654321
        elif scenario == 'forbidden_text':
            data['general_notes'] = 'Toma un suplemento de proteína después de entrenar.'
        return AIResult(data=data, usage=usage, model=FAKE_MODEL)
