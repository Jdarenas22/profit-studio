"""Prompts `/2` y generador simulado con `history` (paso 4 de docs/contracts/plan-ia-adaptacion.md).

Garantías: el texto `/1` no cambia; `/2` es `/1` más UNA regla sobre `history`; solo se usa `/2` cuando
el pedido lleva `history`; el generador simulado respeta el historial sin romper las reglas.
"""
from datetime import timedelta

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from apps.plans import nutrition, services
from apps.plans.ai import fake
from apps.plans.ai import prompts as p
from apps.plans.models import MealChoice, PlanGenerationJob
from apps.plans.rules import foods as f
from apps.plans.rules import meal_validate as m

from .helpers import NutritionScenarioTestCase, PlanScenarioTestCase
from .test_adaptation_data import HistoryMixin, days_ago
from .test_nutrition_rules import ITEMS, LUNCH_KCAL, LUNCH_PROTEIN, slot_payload


class PromptVersionTests(SimpleTestCase):
    def test_version_1_is_untouched(self):
        self.assertNotIn('history', p.EXERCISE_SYSTEM)
        self.assertNotIn('history', p.MEAL_SYSTEM)
        self.assertEqual(p.exercise_prompt(False), (p.EXERCISE_SYSTEM, 'exercise/1'))
        self.assertEqual(p.meal_prompt(False), (p.MEAL_SYSTEM, 'meal/1'))

    def test_version_2_is_version_1_plus_exactly_one_rule(self):
        self.assertEqual(p.EXERCISE_SYSTEM_V2.replace(p.EXERCISE_HISTORY_RULE, ''), p.EXERCISE_SYSTEM)
        self.assertEqual(p.MEAL_SYSTEM_V2.replace(p.MEAL_HISTORY_RULE, ''), p.MEAL_SYSTEM)
        self.assertEqual(p.exercise_prompt(True), (p.EXERCISE_SYSTEM_V2, 'exercise/2'))
        self.assertEqual(p.meal_prompt(True), (p.MEAL_SYSTEM_V2, 'meal/2'))

    def test_the_new_rule_continues_the_numbering_and_stays_before_the_format(self):
        exercise, meal = p.EXERCISE_SYSTEM_V2, p.MEAL_SYSTEM_V2
        self.assertEqual((exercise.count('\n10. '), meal.count('\n11. ')), (1, 1))
        self.assertLess(exercise.index('\n9. '), exercise.index('\n10. '))
        self.assertLess(exercise.index('\n10. '), exercise.index('Formato `exercise-plan/1`'))
        self.assertLess(meal.index('\n10. '), meal.index('\n11. '))
        self.assertLess(meal.index('\n11. '), meal.index('Formato `meal-slot/1`'))

    def test_the_rule_keeps_safety_above_the_history(self):
        for rule in (p.EXERCISE_HISTORY_RULE, p.MEAL_HISTORY_RULE):
            self.assertIn('SUAVES', rule)
            self.assertIn('nunca', rule)
            self.assertIn('anulan', rule)
            self.assertIn('gana la regla', rule)
            self.assertIn('datos, no instrucciones', rule)

    def test_the_rule_never_asks_for_changes_to_calories_or_limits(self):
        for text in (p.EXERCISE_HISTORY_RULE, p.MEAL_HISTORY_RULE):
            self.assertNotIn('suplement', text.lower())
            self.assertNotIn('aumenta las calor', text.lower())
            self.assertNotIn('reduce las calor', text.lower())


# ─── Generador simulado ───────────────────────────────────────────────────────

def exercise_payload(**history):
    payload = {
        'allowed_exercises': [{'id': i, 'name': f'E{i}'} for i in range(1, 13)],
        'limits': {'exercises_per_day_min': 3, 'exercises_per_day_max': 6, 'sets_max': 4},
        'training': {'days_per_week': 3},
    }
    if history:
        payload['history'] = history
    return payload


class FakeExerciseHistoryTests(SimpleTestCase):
    def per_day(self, plan):
        return [len(day['exercises']) for day in plan['days']]

    def test_without_history_nothing_changes(self):
        base = fake.sample_exercise_plan(exercise_payload())
        self.assertEqual(self.per_day(base), [4, 4, 4])
        self.assertEqual(base['general_notes'], 'Calienta 5 minutos antes de cada sesión.')

    def test_low_adherence_means_shorter_simpler_sessions(self):
        plan = fake.sample_exercise_plan(exercise_payload(training={'adherence': 'low'}))
        self.assertEqual(self.per_day(plan), [3, 3, 3])
        self.assertIn('retomar la constancia', plan['general_notes'])
        self.assertEqual(len(plan['days']), 3)                       # los días por semana no cambian

    def test_exercises_the_trainer_removes_are_avoided(self):
        removed = [1, 2, 3, 4]
        plan = fake.sample_exercise_plan(exercise_payload(trainer_edits={'removed_exercise_ids': removed}))
        used = {item['exercise_id'] for day in plan['days'] for item in day['exercises']}
        self.assertFalse(used & set(removed))

    def test_it_never_uses_ids_outside_the_allowed_list(self):
        plan = fake.sample_exercise_plan(exercise_payload(
            training={'adherence': 'high'}, progress='stalled', trainer_edits={'removed_exercise_ids': [99]}))
        used = {item['exercise_id'] for day in plan['days'] for item in day['exercises']}
        self.assertTrue(used <= set(range(1, 13)))

    def test_stalled_with_high_adherence_varies_the_stimulus(self):
        base = fake.sample_exercise_plan(exercise_payload(training={'adherence': 'high'}))
        varied = fake.sample_exercise_plan(exercise_payload(training={'adherence': 'high'}, progress='stalled'))
        self.assertNotEqual(base['days'][0]['exercises'][0]['exercise_id'],
                            varied['days'][0]['exercises'][0]['exercise_id'])
        self.assertIn('renovar el estímulo', varied['general_notes'])


class FakeMealHistoryTests(SimpleTestCase):
    def setUp(self):
        self.catalog = f.filter_foods(ITEMS, f.NutritionProfile())
        self.rules = m.SlotRules(slot_code='lunch', kcal=LUNCH_KCAL, protein_g=LUNCH_PROTEIN,
                                 foods=self.catalog.by_id)

    def options(self, **history):
        payload = slot_payload(self.catalog)
        if history:
            payload['history'] = history
        return fake.sample_meal_slot(payload)['options']

    def a_real_protein(self):
        return next(item.id for item in self.catalog.allowed if item.name.startswith('Pechuga de pollo'))

    def ids(self, options):
        return {item['food_id'] for option in options for item in option['items']}

    def test_an_empty_history_gives_the_same_options_as_no_history(self):
        payload = slot_payload(self.catalog)
        with_empty = dict(payload, history={})
        self.assertEqual(fake.sample_meal_slot(payload), fake.sample_meal_slot(with_empty))

    def test_foods_the_client_avoids_are_not_used(self):
        baseline = self.ids(self.options())
        avoid = sorted(baseline)[:4]
        options = self.options(nutrition={'liked_food_ids': [], 'avoid_food_ids': avoid})
        self.assertEqual(len(options), 3)
        self.assertFalse(self.ids(options) & set(avoid))

    def test_foods_the_trainer_removes_are_not_used_either(self):
        removed = sorted(self.ids(self.options()))[:3]
        options = self.options(trainer_edits={'removed_food_ids': removed})
        self.assertFalse(self.ids(options) & set(removed))

    def test_liked_foods_are_favored(self):
        liked = self.a_real_protein()
        options = self.options(nutrition={'liked_food_ids': [liked], 'avoid_food_ids': []})
        self.assertIn(liked, self.ids(options))

    def test_options_with_history_still_pass_the_real_validation(self):
        avoid = sorted(self.ids(self.options()))[:4]
        liked = self.a_real_protein()
        options = self.options(nutrition={'liked_food_ids': [liked], 'avoid_food_ids': avoid})
        result = m.validate_meal_slot({'schema_version': m.MEAL_SCHEMA_VERSION, 'slot_code': 'lunch',
                                       'options': options}, self.rules)
        self.assertTrue(result.ok, result.codes)

    def test_if_avoiding_would_leave_no_options_it_falls_back_to_all_foods(self):
        everything = [item.id for item in self.catalog.allowed]
        options = self.options(nutrition={'liked_food_ids': [], 'avoid_food_ids': everything})
        self.assertEqual(len(options), 3)


# ─── Integración con los trabajos ─────────────────────────────────────────────

@override_settings(AI_PLANS_ADAPTATION=True)
class ExerciseJobPromptTests(HistoryMixin, PlanScenarioTestCase):
    def test_history_uses_version_2(self):
        self.approve_exercise(self.client_a, days=30)
        job, fake_ai = self.request(self.client_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        self.assertEqual(fake_ai.calls[0]['system'], p.EXERCISE_SYSTEM_V2)
        self.assertEqual(job.plan.prompt_version, 'exercise/2')
        self.assertIn('history', fake_ai.calls[0]['payload'])

    def test_without_history_version_1_is_kept_even_with_the_switch_on(self):
        self.ready(self.client_a)
        job, fake_ai = self.request(self.client_a)
        self.assertEqual(fake_ai.calls[0]['system'], p.EXERCISE_SYSTEM)
        self.assertEqual(job.plan.prompt_version, 'exercise/1')

    def test_the_switch_off_keeps_version_1(self):
        self.approve_exercise(self.client_a, days=30)
        with override_settings(AI_PLANS_ADAPTATION=False):
            job, fake_ai = self.request(self.client_a)
        self.assertEqual(fake_ai.calls[0]['system'], p.EXERCISE_SYSTEM)
        self.assertEqual(job.plan.prompt_version, 'exercise/1')

    def test_the_low_adherence_draft_is_simpler_and_still_valid(self):
        plan = self.approve_exercise(self.client_a, days=30)
        job, _fake = self.request(self.client_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        self.assertIn('retomar la constancia', job.plan.routine.notes)
        self.assertEqual(job.plan.routine.days.count(), plan.routine.days.count())


@override_settings(AI_PLANS_ADAPTATION=True)
class NutritionJobPromptTests(NutritionScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.plan = self.approved_nutrition(self.client_a)
        from apps.plans.models import Plan
        Plan.objects.filter(pk=self.plan.pk).update(approved_at=days_ago(30))

    def new_plan(self):
        with self.fake_ai() as fake_ai, self.captureOnCommitCallbacks(execute=True):
            job, _created = services.request_plan(
                self.client_a, 'nutrition', self.client_a, services.new_request_token())
        job.refresh_from_db()
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        return job, fake_ai

    def test_history_uses_the_meal_prompt_version_2(self):
        job, fake_ai = self.new_plan()
        self.assertTrue(all(call['system'] == p.MEAL_SYSTEM_V2 for call in fake_ai.calls))
        self.assertTrue(all('history' in call['payload'] for call in fake_ai.calls))
        self.assertEqual(job.plan.prompt_version, 'meal/2')

    def test_liked_foods_reach_the_ai_and_the_draft_stays_valid(self):
        slot = self.plan.nutrition.slots.get(code='lunch')
        option = slot.options.get(position=1)
        for ago in (1, 2, 3):
            MealChoice.objects.create(client=self.client_a, slot=slot, option=option,
                                      date=(timezone.localdate() - timedelta(days=ago)), completed_at=timezone.now())
        job, fake_ai = self.new_plan()
        liked = set(option.items.values_list('food_id', flat=True))
        sent = set(fake_ai.calls[0]['payload']['history']['nutrition']['liked_food_ids'])
        self.assertEqual(sent, liked)
        self.assertEqual(job.adaptation_summary['liked_foods'], len(liked))

    def test_the_switch_off_keeps_the_meal_prompt_version_1(self):
        with override_settings(AI_PLANS_ADAPTATION=False):
            job, fake_ai = self.new_plan()
        self.assertTrue(all(call['system'] == p.MEAL_SYSTEM for call in fake_ai.calls))
        self.assertEqual(job.plan.prompt_version, 'meal/1')
