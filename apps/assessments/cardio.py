"""Cálculo puro, sin IA ni red. Entradas: frecuencias enteras en latidos/minuto."""
from decimal import Decimal

FORMULA_VERSION = 'ruffier-dickson/1'
PROTOCOL_VERSION = 'squats-30-45s-recovery-60s/1'
PROTOCOL = {
    'version': PROTOCOL_VERSION,
    'squats': 30,
    'exercise_seconds': 45,
    'recovery_seconds': 60,
    'heart_rate_unit': 'bpm',
    'measurement': 'manual_entry',
}
PULSE_FIELDS = ('resting_hr_bpm', 'post_exercise_hr_bpm', 'recovery_hr_bpm')


def calculate_indices(resting_hr_bpm, post_exercise_hr_bpm, recovery_hr_bpm):
    pulses = (resting_hr_bpm, post_exercise_hr_bpm, recovery_hr_bpm)
    if any(type(value) is not int or not 20 <= value <= 250 for value in pulses):
        raise ValueError('Los tres pulsos deben ser enteros entre 20 y 250 bpm.')
    resting, post, recovery = map(Decimal, pulses)
    return {
        'ruffier_index': ((resting + post + recovery - 200) / 10).quantize(Decimal('0.01')),
        'ruffier_dickson_index': ((post - 70 + 2 * (recovery - resting)) / 10).quantize(Decimal('0.01')),
    }


def serialize_evaluation(evaluation):
    return {
        'id': evaluation.pk,
        'assessment_id': evaluation.assessment_id,
        'request_token': str(evaluation.request_token),
        'created_at': evaluation.created_at.isoformat(),
        **{name: getattr(evaluation, name) for name in PULSE_FIELDS},
        'ruffier_index': str(evaluation.ruffier_index),
        'ruffier_dickson_index': str(evaluation.ruffier_dickson_index),
        'formula_version': evaluation.formula_version,
        'protocol_version': evaluation.protocol_version,
        'protocol_confirmed': evaluation.protocol_confirmed,
        'interpretation': None,
        'interpretation_status': 'professional_review_required',
    }

