from datetime import timedelta

from django.test import SimpleTestCase
from django.urls import reverse

from apps.health import dates
from apps.plans import nutrition, services
from apps.plans.models import MealChoice, Plan, PlanGenerationJob
from apps.plans.rules import weekly
from .helpers import NutritionScenarioTestCase


class RankingTests(SimpleTestCase):
    def test_public_payload_drops_personal_fields(self):
        option = {'name': 'A', 'cuisine': 'cotidiana', 'main_protein': 'huevo',
                  'preparation': 'Servir.', 'items': [{'grams': 100}], 'client': {'weight': 70}}
        payload = weekly.public_payload([option], 'breakfast')
        self.assertEqual(set(payload['recipe_options'][0]), {'name', 'cuisine', 'main_protein', 'preparation'})

    def test_six_compatible_dishes_can_rotate_without_repeating_yesterday(self):
        offered = [{'name': str(i), 'main_protein': str(i), 'preparation': 'Servir.',
                    'items': [{'food_id': i * 2}, {'food_id': i * 2 + 1}]} for i in range(6)]
        days = weekly.schedule(offered)
        self.assertEqual(len(days), 7)
        for before, after in zip(days, days[1:]):
            self.assertFalse({r['name'] for r in before} & {r['name'] for r in after})

    def test_rejects_unknown_duplicated_missing_and_non_string_names(self):
        offered = [{'name': 'A'}, {'name': 'B'}]
        for names in (['A', 'A'], ['A'], ['A', 'X'], ['A', {}]):
            with self.subTest(names=names):
                self.assertFalse(weekly.validate_ranking({'recipe_names': names}, offered).ok)
        self.assertTrue(weekly.validate_ranking({'recipe_names': ['B', 'A']}, offered).ok)

    def test_insufficient_catalogue_is_explicit(self):
        with self.assertRaisesRegex(ValueError, 'weekly_variety_unavailable'):
            weekly.schedule([{'name': str(i)} for i in range(5)])


class WeeklyMenuTests(NutritionScenarioTestCase):
    def test_clone_preserves_week_and_approval_rejects_adjacent_repetition(self):
        from apps.plans import cloning
        plan = self.approved_nutrition(self.client_a, weekly=True)
        clone, created = cloning.clone_plan(plan, self.boss)
        self.assertTrue(created)
        self.assertEqual(clone.nutrition.duration_days, 7)
        self.assertEqual(clone.nutrition.slots.count(), 21)
        self.assertEqual(nutrition.approval_problems(clone), [])
        first = clone.nutrition.slots.get(day_index=1, code='lunch').options.first()
        second = clone.nutrition.slots.get(day_index=2, code='lunch').options.first()
        second.name = first.name
        second.save(update_fields=['name'])
        self.assertTrue(any('repetidos' in text for text in nutrition.approval_problems(clone)))

    def test_review_query_count_does_not_grow_with_seven_days(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        legacy = self.nutrition_draft(self.client_a)
        with CaptureQueriesContext(connection) as queries:
            nutrition.approval_problems(legacy)
        daily_count = len(queries)
        # Separate client avoids the existing pending draft guard.
        week = self.nutrition_draft(self.client_b, weekly=True)
        with CaptureQueriesContext(connection) as queries:
            nutrition.approval_problems(week)
        self.assertLessEqual(len(queries), daily_count + 2)

    def test_seven_days_three_options_each_and_only_five_public_calls(self):
        self.ready(self.client_a, meal_slots=['breakfast', 'mid_morning', 'lunch', 'snack', 'dinner'])
        job, fake = self.request_nutrition(self.client_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        self.assertEqual(len(fake.calls), 5)
        plan = job.plan
        self.assertEqual(plan.nutrition.duration_days, 7)
        self.assertEqual(plan.nutrition.meals_per_day, 5)
        self.assertEqual(plan.nutrition.slots.count(), 35)
        previous = {}
        for slot in plan.nutrition.slots.all():
            names = {option.name for option in slot.options.filter(is_active=True)}
            self.assertEqual(len(names), 3)
            self.assertFalse(previous.get(slot.code, set()) & names)
            previous[slot.code] = names
        for call in fake.calls:
            self.assertEqual(call['task'], 'weekly_meal_slot')
            self.assertEqual(set(call['payload']), {'schema_version', 'slot_code', 'days', 'previous_errors', 'recipe_options'})
            for recipe in call['payload']['recipe_options']:
                self.assertEqual(set(recipe), {'name', 'cuisine', 'main_protein', 'preparation'})
        self.assertEqual(nutrition.approval_problems(plan), [])
        self.login(self.boss)
        response = self.client.get(reverse('trainer_plan_detail', args=[plan.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Día 7')

    def test_fixed_week_day_scoping_expiry_and_client_page(self):
        plan = self.approved_nutrition(self.client_a, weekly=True)
        self.assertEqual(plan.valid_until, dates.today() + timedelta(days=6))
        days = nutrition.meal_dates(plan)
        self.assertEqual(len(days), 7)
        first = nutrition.day_view(self.client_a, plan, days[0])
        second = nutrition.day_view(self.client_a, plan, days[1])
        self.assertEqual(len(first['slots']), 3)
        self.assertTrue(all(entry['slot'].day_index == 2 for entry in second['slots']))
        slot = first['slots'][0]['slot']
        option = first['slots'][0]['options'][0]['option']
        with self.assertRaises(services.PlanStateError):
            nutrition.choose_option(self.client_a, slot.pk, option.pk, days[1])
        self.assertFalse(MealChoice.objects.exists())
        nutrition.choose_option(self.client_a, slot.pk, option.pk, days[0])
        self.login(self.client_a)
        response = self.client.get(reverse('member_nutrition_plan'), {'date': days[1].isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Plan de 7 días')
        self.assertEqual(len(response.context['slots']), 3)
        self.assertEqual(len(response.context['date_pills']), 7)
        rows, assumed = nutrition.shopping_list(self.client_a, plan)
        self.assertTrue(rows)
        self.assertEqual(assumed, 20)  # 7 x 3, minus the explicit choice above.

    def test_no_provider_call_when_variety_is_impossible(self):
        from unittest.mock import patch
        self.ready(self.client_a)
        with patch('apps.plans.jobs.recipes.candidates', return_value=[]):
            job, fake = self.request_nutrition(self.client_a)
        self.assertEqual(job.error_code, 'weekly_variety_unavailable')
        self.assertEqual(fake.calls, [])
        self.assertFalse(Plan.objects.exists())
