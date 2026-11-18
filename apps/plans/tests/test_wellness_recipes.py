"""Pruebas del recetario real, filtros y alcance de bienestar."""
import copy
from dataclasses import replace
from decimal import Decimal

from django.test import SimpleTestCase

from apps.plans import nutrition, services, wellness
from apps.plans.ai.fake import sample_meal_slot
from apps.plans.foods_data import food_rows
from apps.plans.models import Plan
from apps.plans.rules.foods import FoodItem
from apps.plans.rules import recipes, meal_validate as meal
from .helpers import NutritionScenarioTestCase


def rules_for(slot='breakfast', excluded=()):
    foods = {}
    for number, row in enumerate(food_rows(), 1):
        if row['name'] not in excluded:
            foods[number] = FoodItem(
                id=number, name=row['name'], group=row['group'], kcal100=row['kcal100'],
                protein100=row['protein100'], carbs100=row['carbs100'], fat100=row['fat100'])
    return meal.SlotRules(slot_code=slot, kcal=Decimal('500'), protein_g=Decimal('22'), foods=foods)


class RecipeTests(SimpleTestCase):
    def test_international_dishes_and_only_allowed_foods(self):
        rules = rules_for('lunch')
        options = recipes.candidates(rules)
        self.assertGreaterEqual(len(options), 6)
        self.assertTrue(any('Tacos' in option['name'] for option in options))
        self.assertTrue(any('Pasta' in option['name'] for option in options))
        for option in options:
            rows = [(rules.foods[i['food_id']], i['grams']) for i in option['items']]
            self.assertFalse(meal.evaluate_option(rows, rules)[2])

    def test_missing_ingredients_and_no_cooking_do_not_produce_substitutions(self):
        options = recipes.candidates(rules_for(excluded=('Huevo entero cocido',)))
        self.assertFalse(any('huevo' in o['name'].lower() for o in options))
        cold = recipes.candidates(rules_for(), cooking='none')
        self.assertFalse(any('Tostadas' in o['name'] for o in cold))
        self.assertEqual(recipes.candidates(replace(rules_for(), foods={})), [])

    def test_simulated_output_is_not_an_alias_of_catalog(self):
        offered = recipes.candidates(rules_for())
        before = copy.deepcopy(offered)
        result = sample_meal_slot({'recipe_options': offered, 'options_requested': 3, 'slot': {'code': 'breakfast'}})
        self.assertEqual(len(result['options']), 3)
        result['options'][0]['items'][0]['grams'] = 999
        self.assertEqual(offered, before)

    def test_invented_dish_is_rejected_and_preparation_is_canonical(self):
        rules = rules_for()
        options = recipes.candidates(rules)
        rules = replace(rules, recipes=tuple(options))
        result = sample_meal_slot({'recipe_options': options, 'options_requested': 3, 'slot': {'code': 'breakfast'}})
        result['options'][0]['preparation'] = 'Sirve juntos.'
        checked = meal.validate_meal_slot(result, rules)
        self.assertTrue(checked.ok, checked.errors)
        expected = next(o['preparation'] for o in options if o['name'] == result['options'][0]['name'])
        self.assertEqual(checked.data['options'][0]['preparation'], expected)
        result['options'][0]['name'] = 'Queso con fríjoles y naranja'
        self.assertFalse(meal.validate_meal_slot(result, rules).ok)


class WellnessFlowTests(NutritionScenarioTestCase):
    def test_weight_loss_and_muscle_gain_keep_their_calculated_targets(self):
        for client, goal in ((self.client_a, 'weight_loss'), (self.client_b, 'muscle_gain')):
            with self.subTest(goal=goal):
                client.training_goal = goal
                client.save(update_fields=['training_goal'])
                self.ready(client, training_goal=goal,
                           meal_slots=['breakfast', 'mid_morning', 'lunch', 'snack', 'dinner'])
                client.refresh_from_db()
                job, _fake = self.request_nutrition(client)
                self.assertEqual(job.status, job.STATUS_SUCCEEDED, job.error_code)
                adjustment = job.plan.nutrition.adjustment_pct
                self.assertLess(adjustment, 0) if goal == 'weight_loss' else self.assertGreater(adjustment, 0)

    def test_five_meals_with_recipes_and_no_raw_notes(self):
        self.ready(self.client_a, meal_slots=['breakfast', 'mid_morning', 'lunch', 'snack', 'dinner'])
        job, fake = self.request_nutrition(self.client_a, trainer_notes='Texto privado')
        self.assertEqual(job.status, job.STATUS_SUCCEEDED, job.error_code)
        self.assertEqual(len(fake.calls), 5)
        self.assertEqual(job.plan.nutrition.slots.count(), 5)
        for call in fake.calls:
            self.assertIsNone(call['payload']['trainer_notes'])
            self.assertEqual(call['payload']['scope'], 'wellness_food_choices')
            self.assertIn('recipe_options', call['payload'])
            self.assertIn('training', call['payload'])

    def test_clinical_profiles_block_before_external_call(self):
        from .test_nutrition import hypertension_profile
        self.ready(self.client_a, **hypertension_profile())
        job, fake = self.request_nutrition(self.client_a)
        self.assertEqual(job.status, job.STATUS_BLOCKED)
        self.assertIn(wellness.OUT_OF_SCOPE, job.flags)
        self.assertEqual(fake.calls, [])

    def test_existing_plan_cannot_be_approved_or_shown_after_scope_changes(self):
        plan = self.nutrition_draft(self.client_a)
        Plan.objects.filter(pk=plan.pk).update(is_simulated=False)
        plan.refresh_from_db()
        profile = services.health.current_profile(self.client_a)
        profile.conditions = ['hypertension']
        profile.save()
        with self.assertRaises(services.PlanStateError):
            services.approve_plan(plan, self.boss, warnings_ack=True, stale_ack=True)
        Plan.objects.filter(pk=plan.pk).update(status=Plan.STATUS_APPROVED)
        self.assertIsNone(nutrition.member_plan(self.client_a))

