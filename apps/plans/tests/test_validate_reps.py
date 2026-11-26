"""V07: formato de `reps` más tolerante (solo en la forma) y motivos finos para el reintento."""
import re

from django.test import SimpleTestCase

from apps.plans.rules import validate as v


def plan(reps='10-12', n_days=1, per_day=3, repeat=False):
    ids = list(range(1, per_day + 1))
    if repeat:
        ids[1] = ids[0]
    days = [{'name': f'Día {d}', 'exercises': [
        {'exercise_id': i, 'sets': 3, 'reps': reps, 'rest_seconds': 60, 'observations': ''} for i in ids]}
        for d in range(1, n_days + 1)]
    return {'schema_version': v.EXERCISE_SCHEMA_VERSION, 'routine_name': 'Rutina', 'general_notes': '', 'days': days}


RULES = v.ExerciseRules(allowed_ids=frozenset(range(1, 12)), days=1, session_minutes=60)


class NormalizeRepsTests(SimpleTestCase):
    def test_equivalent_formats_are_normalized(self):
        casos = {
            '12': '12', '10-12': '10-12', '10 a 12': '10-12', '10–12': '10-12', '10 — 12': '10-12',
            '12 repeticiones': '12', '12 reps': '12', '1 repetición': '1', '1 repeticion': '1', '12 veces': '12',
            '30 segundos': '30 seg', '30 seg.': '30 seg', '30s': '30 seg', '  12  ': '12',
        }
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(v.normalize_reps(entrada), esperado)

    def test_per_side_meaning_is_kept_and_reaches_the_routine(self):
        casos = {
            '12 por lado': '12 por lado', '12-15 por pierna': '12-15 por lado', '10 c/u': '10 por lado',
            '12 repeticiones por lado': '12 por lado', '12 cada brazo': '12 por lado',
            '30 seg por lado': '30 seg por lado', '30 segundos por lado': '30 seg por lado',
        }
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(v.normalize_reps(entrada), esperado)
                result = v.validate_exercise_plan(plan(reps=entrada), RULES)
                self.assertTrue(result.ok, result.errors)
                self.assertEqual(result.data['days'][0]['exercises'][0]['reps'], esperado)   # llega a la rutina
                self.assertLessEqual(len(esperado), 50)                                       # cabe en RoutineExercise.reps

    def test_normalized_formats_pass_the_validator(self):
        for reps in ('10 a 12', '12–15', '12 repeticiones', '1 repetición', '30 segundos'):
            with self.subTest(reps=reps):
                result = v.validate_exercise_plan(plan(reps=reps), RULES)
                self.assertTrue(result.ok, result.errors)
                self.assertRegex(result.data['days'][0]['exercises'][0]['reps'], v.REPS_RE)

    def test_nonsense_is_still_rejected(self):
        for reps in ('hasta el fallo', 'muchas', '100-200', '8 a doce', '', '12 kg',
                     '30 segundos de plancha', '30 seg y descansa', '12 por lado y subir carga'):
            with self.subTest(reps=reps):
                result = v.validate_exercise_plan(plan(reps=reps), RULES)
                self.assertFalse(result.ok)
                self.assertEqual(result.codes, ('V07_EXERCISE',))
                self.assertIn('V07_REPS_FORMAT', result.reasons)


class RetryReasonsTests(SimpleTestCase):
    def test_each_v07_rule_has_its_own_reason(self):
        casos = {
            'V07_DAYS_COUNT': plan(n_days=2),
            'V07_PER_DAY_COUNT': plan(per_day=2),
            'V07_REPEATED': plan(repeat=True),
        }
        for reason, data in casos.items():
            with self.subTest(reason=reason):
                result = v.validate_exercise_plan(data, RULES)
                self.assertEqual(result.retry_codes, ('V07_EXERCISE', reason))   # exactamente el código y su motivo

    def test_sets_and_rest_reasons(self):
        data = plan()
        data['days'][0]['exercises'][0]['sets'] = 99
        data['days'][0]['exercises'][1]['rest_seconds'] = 5
        self.assertEqual(v.validate_exercise_plan(data, RULES).reasons, ('V07_SETS_RANGE', 'V07_REST_RANGE'))

    def test_reasons_are_only_safe_codes(self):
        data = plan(reps='basura', n_days=2, per_day=2, repeat=True)
        for code in v.validate_exercise_plan(data, RULES).retry_codes:
            self.assertRegex(code, r'^[A-Z0-9_]+$')   # lo único que pasa el filtro del nivel gratuito

    def test_other_codes_have_no_reasons(self):
        data = plan()
        data['days'][0]['exercises'][0]['exercise_id'] = 999
        result = v.validate_exercise_plan(data, RULES)
        self.assertEqual(result.codes, ('V02_IDS',))
        self.assertEqual(result.reasons, ())
