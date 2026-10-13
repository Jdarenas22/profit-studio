"""Reglas puras del perfil de adaptación (paso 1 de docs/contracts/plan-ia-adaptacion.md): sin base
de datos ni red."""
import json
from datetime import date, timedelta

from django.test import SimpleTestCase

from apps.plans.foods_data import food_rows
from apps.plans.rules import adaptation as a
from apps.plans.rules import catalog as c
from apps.plans.rules import foods as f

TODAY = date(2026, 11, 10)
DAYS = (a.TrainingDay(1, 'Día 1: Pierna', 1), a.TrainingDay(2, 'Día 2: Torso', 2), a.TrainingDay(3, 'Día 3: Glúteo', 3))


def ago(days):
    return TODAY - timedelta(days=days)


def make(**overrides):
    base = dict(today=TODAY, history_since=ago(28), goal='weight_loss', days_per_week=3, training_days=DAYS)
    base.update(overrides)
    return a.AdaptationInput(**base)


def logs(day_id, count):
    """`count` registros de un día de rutina dentro de la ventana de 28 días."""
    return tuple(a.TrainingLog(day_id, ago(1 + 2 * i)) for i in range(count))


def weights(*pairs):
    return tuple(a.WeightPoint(ago(days), kg) for days, kg in pairs)


class ColdStartTests(SimpleTestCase):
    def test_never_had_an_approved_plan(self):
        profile = a.build_profile(make(history_since=None))
        self.assertEqual(profile, a.COLD_START)
        self.assertFalse(profile.ready)
        self.assertIsNone(profile.as_payload())

    def test_less_than_14_days_of_history(self):
        data = make(history_since=ago(13), training_logs=logs(1, 5), completed_meals=({1}, {1}, {1}))
        self.assertIsNone(a.build_profile(data).as_payload())
        self.assertTrue(a.build_profile(make(history_since=ago(14))).ready)

    def test_ready_without_signals_has_no_payload(self):
        profile = a.build_profile(make(days_per_week=0, goal='cardio', training_days=()))
        self.assertTrue(profile.ready)
        self.assertFalse(profile.has_signal)
        self.assertIsNone(profile.as_payload())


class AdherenceTests(SimpleTestCase):
    def adherence(self, count):
        return a.training_adherence(make(training_logs=logs(1, count)))

    def test_levels(self):
        # 3 días por semana x 4 semanas = 12 esperados
        self.assertEqual(self.adherence(0), 'low')
        self.assertEqual(self.adherence(5), 'low')            # 41 %
        self.assertEqual(self.adherence(6), 'medium')         # 50 %
        self.assertEqual(self.adherence(9), 'medium')         # 75 %
        self.assertEqual(self.adherence(10), 'high')          # 83 %
        self.assertEqual(self.adherence(12), 'high')

    def test_cannot_exceed_100_percent_and_logs_outside_the_window_do_not_count(self):
        data = make(training_logs=logs(1, 12) + (a.TrainingLog(1, ago(40)), a.TrainingLog(1, ago(35))))
        self.assertEqual(a.training_adherence(data), 'high')
        old_only = make(training_logs=(a.TrainingLog(1, ago(40)),) * 12)
        self.assertEqual(a.training_adherence(old_only), 'low')

    def test_window_starts_at_the_first_plan(self):
        # 14 días de historial: esperados = 3 x 2 = 6 (no 12)
        data = make(history_since=ago(14), training_logs=logs(1, 5))
        self.assertEqual(a.training_adherence(data), 'high')

    def test_no_days_per_week_means_no_adherence(self):
        self.assertIsNone(a.training_adherence(make(days_per_week=0)))


class SkippedDaysTests(SimpleTestCase):
    def test_only_the_rarely_done_day_is_reported(self):
        data = make(training_logs=logs(1, 4) + logs(2, 2) + logs(3, 1))
        self.assertEqual(a.skipped_days(data), ('Día 3: Glúteo',))     # 1/4 = 25 %; el día 2 está en 50 %

    def test_at_most_two_ordered_by_rate_then_order(self):
        data = make(training_logs=logs(1, 4))
        self.assertEqual(a.skipped_days(data), ('Día 2: Torso', 'Día 3: Glúteo'))
        four = DAYS + (a.TrainingDay(4, 'Día 4', 4),)
        self.assertEqual(len(a.skipped_days(make(training_days=four, training_logs=logs(1, 4)))), 2)

    def test_needs_three_full_weeks(self):
        data = make(history_since=ago(20), training_logs=logs(1, 3))
        self.assertEqual(a.skipped_days(data), ())

    def test_ignores_logs_of_days_not_in_the_routine(self):
        data = make(training_logs=logs(99, 12))
        self.assertEqual(len(a.skipped_days(data)), 2)


class FoodPreferenceTests(SimpleTestCase):
    def test_liked_needs_three_completed_meals(self):
        data = make(completed_meals=({1, 2}, {1, 2}, {1, 3}, {2}))
        self.assertEqual(a.food_preferences(data), ((1, 2), ()))

    def test_avoid_needs_two_discards_and_never_eaten(self):
        data = make(discarded_options=({5, 1}, {5, 6}, {1, 7}), completed_meals=({1},))
        liked, avoid = a.food_preferences(data)
        self.assertEqual(avoid, (5,))                 # 1 se descartó 2 veces pero también lo comió
        self.assertEqual(liked, ())

    def test_a_food_counts_once_per_meal_even_if_repeated(self):
        data = make(completed_meals=([1, 1, 1],))
        self.assertEqual(a.food_preferences(data), ((), ()))

    def test_liked_and_avoid_never_overlap(self):
        data = make(completed_meals=({1}, {1}, {1}), discarded_options=({1}, {1}, {2}, {2}))
        liked, avoid = a.food_preferences(data)
        self.assertFalse(set(liked) & set(avoid))
        self.assertEqual((liked, avoid), ((1,), (2,)))


class TrainerCorrectionTests(SimpleTestCase):
    def test_repeated_removals_across_plans(self):
        removals = (a.TrainerRemoval(exercise_ids=frozenset({7, 8}), food_ids=frozenset({3})),
                    a.TrainerRemoval(exercise_ids=frozenset({7}), food_ids=frozenset({3, 4})),
                    a.TrainerRemoval())
        self.assertEqual(a.trainer_corrections(make(trainer_removals=removals)), ((7,), (3,)))

    def test_one_plan_is_not_a_pattern(self):
        removals = (a.TrainerRemoval(exercise_ids=frozenset({7})),)
        self.assertEqual(a.trainer_corrections(make(trainer_removals=removals)), ((), ()))


class ProgressTests(SimpleTestCase):
    def status(self, goal, *pairs):
        return a.progress_status(make(goal=goal, weights=weights(*pairs)))

    def test_weight_loss(self):
        self.assertEqual(self.status('weight_loss', (28, 70), (0, 68)), 'on_track')       # -0.5 kg/semana
        self.assertEqual(self.status('weight_loss', (28, 70), (0, 70.1)), 'stalled')
        self.assertEqual(self.status('weight_loss', (28, 70), (0, 71)), 'off_track')      # sube
        self.assertEqual(self.status('weight_loss', (28, 80), (0, 72)), 'off_track')      # -2 kg/semana: muy rápido

    def test_muscle_gain(self):
        self.assertEqual(self.status('muscle_gain', (28, 60), (0, 61)), 'on_track')
        self.assertEqual(self.status('muscle_gain', (28, 60), (0, 63)), 'off_track')      # +0.75 kg/semana
        self.assertEqual(self.status('muscle_gain', (28, 60), (0, 58)), 'off_track')

    def test_other_goals_are_not_judged_by_weight(self):
        self.assertEqual(self.status('cardio', (28, 70), (0, 65)), 'not_applicable')
        self.assertEqual(self.status('', (28, 70), (0, 65)), 'not_applicable')

    def test_not_enough_measurements(self):
        self.assertEqual(self.status('weight_loss', (0, 70)), 'insufficient_data')
        self.assertEqual(self.status('weight_loss', (7, 70), (0, 69)), 'insufficient_data')   # cubre menos de 14 días
        self.assertEqual(self.status('weight_loss', (60, 75), (50, 74)), 'insufficient_data')  # fuera de los 42 días

    def test_slope_uses_all_points_not_just_the_ends(self):
        # Un pico en el medio no cambia la tendencia general a la baja
        change = a.weekly_weight_change(make(weights=weights((28, 70), (21, 69.5), (14, 71), (7, 68.5), (0, 68))))
        self.assertLess(change, -0.3)


class CalorieReviewTests(SimpleTestCase):
    STALLED = weights((28, 70), (0, 70))

    def build(self, **overrides):
        return a.build_profile(make(weights=self.STALLED, training_logs=logs(1, 12), **overrides))

    def test_suggested_when_stalled_with_high_adherence(self):
        profile = self.build()
        self.assertEqual((profile.adherence, profile.progress), ('high', 'stalled'))
        self.assertTrue(profile.suggest_calorie_review)
        self.assertIn('calorie_review_suggested', profile.reasons)

    def test_not_suggested_with_low_adherence(self):
        profile = a.build_profile(make(weights=self.STALLED, training_logs=logs(1, 2)))
        self.assertFalse(profile.suggest_calorie_review)

    def test_not_suggested_with_less_than_three_weeks(self):
        data = make(history_since=ago(20), weights=weights((16, 70), (0, 70)), training_logs=logs(1, 8))
        profile = a.build_profile(data)
        self.assertEqual(profile.progress, 'stalled')
        self.assertFalse(profile.suggest_calorie_review)

    def test_not_suggested_for_goals_that_weight_cannot_judge(self):
        self.assertFalse(self.build(goal='toning').suggest_calorie_review)

    def test_it_only_suggests_it_never_returns_a_calorie_change(self):
        self.assertFalse(any('kcal' in key or 'calor' in key for key in self.build().as_payload()))


class OutputTests(SimpleTestCase):
    def full(self):
        return a.build_profile(make(
            training_logs=logs(1, 4) + logs(2, 2) + logs(3, 1), completed_meals=({1, 2},) * 3,
            discarded_options=({9},) * 2, weights=weights((28, 70), (0, 68)),
            trainer_removals=(a.TrainerRemoval(exercise_ids=frozenset({7})),) * 2))

    def test_payload_shape(self):
        payload = self.full().as_payload()
        self.assertEqual(set(payload), {'weeks_of_data', 'training', 'nutrition', 'trainer_edits', 'progress'})
        self.assertEqual(payload['training']['skipped_day_names'], ['Día 3: Glúteo'])
        self.assertEqual(payload['nutrition'], {'liked_food_ids': [1, 2], 'avoid_food_ids': [9]})
        self.assertEqual(payload['trainer_edits'], {'removed_exercise_ids': [7], 'removed_food_ids': []})
        self.assertEqual(payload['progress'], 'on_track')
        self.assertEqual(payload['weeks_of_data'], 4)
        json.dumps(payload)                                         # serializable

    def test_payload_only_has_enums_numbers_and_catalog_names(self):
        def strings(value):
            if isinstance(value, dict):
                for item in value.values():
                    yield from strings(item)
            elif isinstance(value, list):
                for item in value:
                    yield from strings(item)
            elif isinstance(value, str):
                yield value
        allowed = {'low', 'medium', 'high', 'on_track', 'stalled', 'off_track', 'insufficient_data',
                   'not_applicable', 'Día 3: Glúteo'}
        self.assertLessEqual(set(strings(self.full().as_payload())), allowed)

    def test_summary_has_codes_and_counts_but_no_names(self):
        summary = self.full().summary()
        self.assertEqual(summary['version'], 'adaptation/1')
        self.assertEqual((summary['skipped_days'], summary['liked_foods'], summary['avoided_foods']), (1, 2, 1))
        self.assertNotIn('Glúteo', json.dumps(summary, ensure_ascii=False))
        self.assertIn('skipped_days', summary['reasons'])

    def test_profile_is_immutable(self):
        with self.assertRaises(Exception):
            self.full().adherence = 'low'

    def test_same_input_same_output(self):
        self.assertEqual(self.full(), self.full())


def food_items():
    return tuple(
        f.FoodItem(id=index + 1, name=row['name'], group=row['group'], kcal100=row['kcal100'],
                   protein100=row['protein100'], carbs100=row['carbs100'], fat100=row['fat100'],
                   allergen_tags=tuple(row['allergen_tags']), diet_tags=tuple(row['diet_tags']))
        for index, row in enumerate(food_rows()))


def exercise_items(n=10):
    return tuple(c.ExerciseItem(id=i, name=f'Ejercicio {i}', intensity='moderate') for i in range(1, n + 1))


class ExclusionTests(SimpleTestCase):
    def test_foods_are_removed_when_enough_remain(self):
        items = food_items()
        kept, removed = a.exclude_foods(items, [items[0].id, items[1].id])
        self.assertEqual(removed, (items[0].id, items[1].id))
        self.assertEqual(len(kept), len(items) - 2)
        self.assertTrue({item.id for item in kept} <= {item.id for item in items})

    def test_foods_are_kept_when_the_catalog_would_be_too_small(self):
        items = food_items()
        avoid = [item.id for item in items[: len(items) - (f.MIN_ALLOWED_FOODS - 1)]]
        kept, removed = a.exclude_foods(items, avoid)
        self.assertEqual((kept, removed), (items, ()))

    def test_foods_unknown_ids_and_empty_lists_change_nothing(self):
        items = food_items()
        self.assertEqual(a.exclude_foods(items, [99999]), (items, ()))
        self.assertEqual(a.exclude_foods(items, []), (items, ()))

    def test_exercises_removed_only_while_enough_remain(self):
        items = exercise_items(10)
        kept, removed = a.exclude_exercises(items, [1, 2])
        self.assertEqual((len(kept), removed), (8, (1, 2)))
        kept, removed = a.exclude_exercises(items, [1, 2, 3, 4, 5])      # quedarían 5 < 6
        self.assertEqual((kept, removed), (items, ()))

    def test_exclusions_never_add_items(self):
        items = exercise_items(10)
        kept, _ = a.exclude_exercises(items, [1, 99])
        self.assertTrue({i.id for i in kept} <= {i.id for i in items})
