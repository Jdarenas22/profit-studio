"""Cálculos puros del progreso (fase D, docs/contracts/plan-ia-fase-d.md, sección 6): ventana y semanas,
constancia, línea base, gráficos. Sin base de datos."""
import re
from datetime import date, timedelta
from decimal import Decimal

from django.test import SimpleTestCase

from apps.plans import texts
from apps.plans.rules import adaptation as rules_adaptation
from apps.plans.rules import progress as rules

TODAY = date(2026, 11, 18)
D = Decimal


def days_ago(*offsets):
    return [TODAY - timedelta(days=offset) for offset in offsets]


def block(done_offsets, *, since_days_ago=60, per_week=3, has_plan=True):
    since = TODAY - timedelta(days=since_days_ago) if since_days_ago is not None else None
    result, _in_window = rules.adherence_block(
        days=days_ago(*done_offsets), today=TODAY, since=since, has_plan=has_plan, per_week=per_week,
        plan_note=texts.NOTE_NO_EXERCISE_PLAN, level_texts=texts.TRAINING_LEVEL_TEXT)
    return result


class WindowAndWeeksTests(SimpleTestCase):
    def test_window_starts_28_days_back_or_at_the_first_plan(self):
        self.assertEqual(rules.window_start(TODAY, None), TODAY - timedelta(days=28))
        self.assertEqual(rules.window_start(TODAY, TODAY - timedelta(days=60)), TODAY - timedelta(days=28))
        self.assertEqual(rules.window_start(TODAY, TODAY - timedelta(days=10)), TODAY - timedelta(days=10))

    def test_four_weeks_cover_exactly_the_window(self):
        ranges = rules.week_ranges(TODAY)
        self.assertEqual([index for index, _first, _last in ranges], [1, 2, 3, 4])
        self.assertEqual(ranges[3], (4, TODAY - timedelta(days=6), TODAY))
        self.assertEqual(ranges[0][1], TODAY - timedelta(days=27))
        for (_i, _f, last), (_j, next_first, _l) in zip(ranges, ranges[1:]):
            self.assertEqual(last + timedelta(days=1), next_first)          # sin huecos ni solapes
        for _index, first, last in ranges:
            self.assertEqual((last - first).days, 6)

    def test_the_boundary_days_of_week_3_and_4(self):
        full = rules.adherence(
            today=TODAY, training_days=days_ago(7, 6), meal_days=[], training_since=TODAY - timedelta(days=60),
            meals_since=None, exercise_plan_ever=True, days_per_week=3, meals_per_day=None,
            has_current_nutrition=False, meals_visible=False)
        weeks = {week['index']: week['training_done'] for week in full['weeks']}
        self.assertEqual(weeks, {1: 0, 2: 0, 3: 1, 4: 1})                  # hoy-7 es de la semana 3; hoy-6, de la 4
        self.assertEqual(full['start'], TODAY - timedelta(days=27))
        self.assertEqual((full['end'], full['days']), (TODAY, 28))

    def test_marks_outside_the_window_do_not_count(self):
        result = block([28, 27, 0], per_week=3)                            # hoy-28 queda fuera (inicio < día)
        self.assertEqual(result['done'], 2)

    def test_marks_before_the_first_plan_do_not_count(self):
        result = block([20, 5], since_days_ago=15, per_week=3)
        self.assertEqual(result['done'], 1)


class AdherenceBlockTests(SimpleTestCase):
    def test_cold_start_has_counts_but_no_rate_with_13_days_and_has_rate_with_14(self):
        cold = block([1, 2, 3], since_days_ago=13)
        self.assertEqual((cold['done'], cold['rate'], cold['level'], cold['expected']), (3, None, None, None))
        self.assertEqual(cold['note'], texts.NOTE_COLD_START)
        ready = block([1, 2, 3], since_days_ago=14)
        self.assertIsNotNone(ready['rate'])
        self.assertEqual(ready['note'], '')

    def test_no_plan_and_no_target_give_a_note_instead_of_a_rate(self):
        no_plan = block([1], since_days_ago=None, has_plan=False)
        self.assertEqual((no_plan['rate'], no_plan['note'], no_plan['has_plan']),
                         (None, texts.NOTE_NO_EXERCISE_PLAN, False))
        self.assertEqual(no_plan['done'], 1)                               # una rutina a mano también cuenta
        no_target = block([1, 2], per_week=None)
        self.assertEqual((no_target['rate'], no_target['note']), (None, texts.NOTE_NO_PROFILE))
        self.assertEqual(no_target['done'], 2)

    def test_rate_is_capped_at_100_and_level_cuts_are_exact(self):
        self.assertEqual(block(list(range(28)), per_week=2)['rate'], 100)  # 28 marcas contra 8 esperadas
        # per_week=2 en 28 días = 8 esperadas: 4 -> 50 % (media), 3 -> 37,5 % (baja)
        self.assertEqual(block([1, 2, 3, 4], per_week=2)['level'], 'medium')
        self.assertEqual(block([1, 2, 3], per_week=2)['level'], 'low')
        # per_week=5 en 28 días = 20 esperadas: 16 -> 80 % (alta), 15 -> 75 % (media)
        self.assertEqual(block(list(range(16)), per_week=5)['level'], 'high')
        self.assertEqual(block(list(range(15)), per_week=5)['level'], 'medium')
        self.assertEqual(block(list(range(16)), per_week=5)['level_text'], texts.TRAINING_LEVEL_TEXT['high'])

    def test_expected_is_rounded_and_per_week_is_reported(self):
        result = block([1], per_week=3)
        self.assertEqual((result['expected'], result['per_week']), (12, 3))

    def test_level_matches_the_adaptation_module_for_the_same_data(self):
        """Mismos datos -> mismo nivel que `rules.adaptation.training_adherence` (para que el panel del
        plan y esta pantalla no se contradigan)."""
        cases = [(done, per_week, since_days)
                 for done in (0, 1, 3, 4, 6, 8, 9, 12, 16, 20, 40)
                 for per_week in (1, 2, 3, 5, 6)
                 for since_days in (14, 20, 28, 45, 90)]
        for done, per_week, since_days in cases:
            with self.subTest(done=done, per_week=per_week, since_days=since_days):
                offsets = [offset % 28 for offset in range(done)]
                since = TODAY - timedelta(days=since_days)
                # las marcas repetidas el mismo día también cuentan (una fila por día de rutina marcado)
                mine = block(offsets, since_days_ago=since_days, per_week=per_week)
                data = rules_adaptation.AdaptationInput(
                    today=TODAY, history_since=since, days_per_week=per_week,
                    training_logs=tuple(rules_adaptation.TrainingLog(day_id=1, on=TODAY - timedelta(days=o))
                                        for o in offsets))
                self.assertEqual(mine['level'], rules_adaptation.training_adherence(data))


class BaselineTests(SimpleTestCase):
    def point(self, pk, offset, weight, *, waist=None, hip=None, imc=None, review=False):
        return rules.MeasurementPoint(pk=pk, date=TODAY - timedelta(days=offset), weight=D(weight),
                                      waist_cm=D(waist) if waist else None, hip_cm=D(hip) if hip else None,
                                      imc=D(imc) if imc else None, needs_review=review)

    def test_picks_the_oldest_measurement_and_prefers_exercise_on_a_tie(self):
        old, new = self.point(1, 60, '70'), self.point(2, 30, '68')
        self.assertEqual(rules.pick_baseline([('nutrition', 2, new), ('exercise', 1, old)])[:2], ('exercise', 1))
        self.assertEqual(rules.pick_baseline([('exercise', 3, new), ('nutrition', 2, old)])[:2], ('nutrition', 2))
        tie = [('nutrition', 5, old), ('exercise', 4, old)]
        self.assertEqual(rules.pick_baseline(tie)[:2], ('exercise', 4))
        self.assertIsNone(rules.pick_baseline([]))

    def test_deltas_use_the_latest_reliable_measurement(self):
        ref = self.point(1, 60, '70.0', waist='80.0', imc='25.00')
        points = [self.point(4, 1, '90.0', review=True), self.point(3, 5, '67.5', waist='78.0', imc='24.10'), ref]
        info = rules.baseline_info(('exercise', 2, ref), points)
        self.assertEqual(info['delta']['weight'], D('-2.5'))
        self.assertEqual(info['delta']['waist_cm'], D('-2.0'))
        self.assertIsNone(info['delta']['hip_cm'])                         # falta en alguno de los dos
        self.assertEqual((info['plan_kind'], info['plan_version'], info['days_between']), ('exercise', 2, 55))
        self.assertFalse(info['same_measurement'])
        self.assertEqual(info['latest_date'], TODAY - timedelta(days=5))

    def test_same_measurement_means_nothing_new(self):
        ref = self.point(1, 10, '70')
        info = rules.baseline_info(('nutrition', 1, ref), [ref])
        self.assertTrue(info['same_measurement'])
        self.assertEqual(set(info['delta'].values()), {None})
        flagged_only = rules.baseline_info(('nutrition', 1, ref), [ref, self.point(2, 1, '80', review=True)])
        self.assertTrue(flagged_only['same_measurement'])
        self.assertIsNone(rules.baseline_info(None, [ref]))

    def test_measure_due_at_the_14_day_edge(self):
        self.assertEqual(rules.measure_due([], TODAY), (True, 'Todavía no tiene medidas registradas.'))
        self.assertEqual(rules.measure_due([self.point(1, 14, '70')], TODAY), (False, ''))
        self.assertEqual(rules.measure_due([self.point(1, 15, '70')], TODAY),
                         (True, 'Hace 15 días que no registra medidas.'))


class ChartTests(SimpleTestCase):
    def rows(self, *items):
        return [(pk, TODAY - timedelta(days=offset), None if value is None else D(value), False)
                for pk, offset, value in items]

    def chart(self, *items, **kwargs):
        return rules.build_chart(key='weight', label='Peso', unit='kg', rows=self.rows(*items), **kwargs)

    def test_no_data(self):
        chart = self.chart((1, 3, None))
        self.assertEqual((chart['count'], chart['has_chart'], chart['points'], chart['polyline']), (0, False, [], ''))
        self.assertEqual(chart['summary'], 'Sin datos de peso.')
        self.assertEqual((chart['y_ticks'], chart['x_ticks']), ([], []))
        imc = rules.build_chart(key='imc', label='IMC', unit='', rows=[])
        self.assertEqual(imc['summary'], 'Sin datos de IMC.')

    def test_single_point_is_centered_with_no_line(self):
        chart = self.chart((1, 3, '74.0'))
        self.assertEqual((chart['count'], chart['has_chart'], chart['polyline']), (1, False, ''))
        self.assertEqual(chart['points'][0]['x'], '340.0')
        self.assertEqual(len(chart['x_ticks']), 1)
        self.assertEqual(chart['summary'], f"Peso: una sola medición, 74,0 kg ({(TODAY - timedelta(days=3)):%d/%m/%Y}).")

    def test_two_points_summary_ticks_and_line(self):
        first, last = date(2026, 10, 3), date(2026, 10, 31)
        chart = rules.build_chart(key='weight', label='Peso', unit='kg', rows=[
            (1, first, D('74.0'), False), (2, last, D('72.5'), False)], baseline_pk=1)
        self.assertTrue(chart['has_chart'])
        self.assertEqual(chart['summary'], 'Peso: de 74,0 kg (03/10/2026) a 72,5 kg (31/10/2026); cambió -1,5 kg.')
        self.assertEqual([tick['label'] for tick in chart['x_ticks']], ['03/10/2026', '31/10/2026'])
        self.assertEqual(chart['points'][0]['x'], '56.0')
        self.assertEqual(chart['points'][1]['x'], '624.0')
        self.assertEqual(chart['polyline'], ' '.join(f"{p['x']},{p['y']}" for p in chart['points']))
        self.assertEqual([p['is_baseline'] for p in chart['points']], [True, False])
        self.assertEqual(chart['points'][0]['text'], '74,0 kg el 03/10/2026')
        self.assertEqual(len(chart['y_ticks']), 3)
        # el valor más alto queda más arriba (y menor) que el más bajo
        self.assertLess(float(chart['points'][0]['y']), float(chart['points'][1]['y']))

    def test_equal_values_and_gaps(self):
        flat = self.chart((1, 10, '70.0'), (2, 5, '70.0'))
        self.assertEqual(len({p['y'] for p in flat['points']}), 1)
        self.assertTrue(all(16 <= float(p['y']) <= 200 for p in flat['points']))
        gaps = self.chart((1, 10, '70.0'), (2, 7, None), (3, 2, '71.0'))
        self.assertEqual((gaps['count'], len(gaps['points'])), (2, 2))     # la medida sin dato se salta
        rise = self.chart((1, 10, '70.0'), (2, 2, '71.5'))
        self.assertIn('cambió +1,5 kg.', rise['summary'])

    def test_coordinates_are_plain_text_with_a_dot_never_floats(self):
        chart = self.chart((1, 20, '70.3'), (2, 13, '69.8'), (3, 6, '71.1'), (4, 1, '68.4'))
        pattern = re.compile(r'^\d{1,3}\.\d$')
        for point in chart['points']:
            self.assertIsInstance(point['x'], str)
            self.assertIsInstance(point['y'], str)
            self.assertRegex(point['x'], pattern)
            self.assertRegex(point['y'], pattern)
        for tick in chart['y_ticks']:
            self.assertRegex(tick['y'], pattern)
        for tick in chart['x_ticks']:
            self.assertRegex(tick['x'], pattern)
        self.assertNotIn(',', chart['polyline'].replace(',', '', chart['polyline'].count(' ') + 1))
        self.assertEqual((chart['view_box'], chart['plot']),
                         ('0 0 640 240', {'left': '56', 'right': '624', 'top': '16', 'bottom': '200'}))

    def test_needs_review_is_carried_to_the_point(self):
        chart = rules.build_chart(key='weight', label='Peso', unit='kg', rows=[
            (1, date(2026, 10, 1), D('70'), False), (2, date(2026, 10, 2), D('90'), True)])
        self.assertEqual([p['needs_review'] for p in chart['points']], [False, True])
