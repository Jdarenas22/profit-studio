"""Plan de alimentación sin IA: el orden de las recetas lo da Python (NUTRITION_RANKING_MODE=rules)."""
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from apps.plans import nutrition, services
from apps.plans.models import KIND_NUTRITION, MealOption, Plan, PlanGenerationJob
from apps.plans.rules import weekly

from .helpers import NutritionScenarioTestCase


def recipe(name, protein, *foods, cuisine='colombiana'):
    return {'name': name, 'cuisine': cuisine, 'main_protein': protein, 'preparation': 'x',
            'items': [{'food_id': food_id, 'grams': 100} for food_id in foods]}


GROUPS = {1: 'verduras', 2: 'cereales', 3: 'azucares', 4: 'frutas', 5: 'proteina_animal', 6: 'bebidas'}


class RankByRulesTests(SimpleTestCase):
    def test_keeps_every_recipe_exactly_once(self):
        options = [recipe(f'r{i}', 'pollo' if i % 2 else 'huevo', 1, 2) for i in range(9)]
        ranked = weekly.rank_by_rules(options, GROUPS)
        self.assertEqual(sorted(r['name'] for r in ranked), sorted(r['name'] for r in options))
        self.assertEqual(len(ranked), len(options))

    def test_is_deterministic(self):
        options = [recipe(f'r{i}', ('pollo', 'huevo', 'res')[i % 3], (1, 2, 3)[i % 3]) for i in range(9)]
        self.assertEqual(weekly.rank_by_rules(list(options), GROUPS), weekly.rank_by_rules(list(options), GROUPS))

    def test_vegetables_rank_higher_and_sugars_lower_within_a_protein(self):
        verduras = recipe('con verduras', 'pollo', 5, 1)
        neutra = recipe('neutra', 'pollo', 5, 2)
        dulce = recipe('dulce', 'pollo', 5, 3)
        ranked = weekly.rank_by_rules([dulce, neutra, verduras], GROUPS)
        self.assertEqual([r['name'] for r in ranked], ['con verduras', 'neutra', 'dulce'])

    def test_proteins_are_interleaved_so_the_top_is_not_all_one_family(self):
        options = [recipe(f'pollo{i}', 'pollo', 1) for i in range(4)] + \
                  [recipe(f'huevo{i}', 'huevo', 1) for i in range(2)] + [recipe('res0', 'res', 1)]
        top = [r['main_protein'] for r in weekly.rank_by_rules(options, GROUPS)[:3]]
        self.assertEqual(len(set(top)), 3)

    def test_ties_keep_the_input_order(self):
        # `recipes.candidates` baraja por trabajo: a igual puntaje se respeta ese orden
        options = [recipe('b', 'pollo', 2), recipe('a', 'pollo', 2), recipe('c', 'pollo', 2)]
        self.assertEqual([r['name'] for r in weekly.rank_by_rules(options, GROUPS)], ['b', 'a', 'c'])

    def test_consecutive_recipes_do_not_share_cuisine_when_it_can_be_avoided(self):
        options = [recipe('a1', 'pollo', 1, cuisine='italiana'), recipe('a2', 'pollo', 1, cuisine='italiana'),
                   recipe('b1', 'pollo', 1, cuisine='colombiana'), recipe('b2', 'pollo', 1, cuisine='colombiana')]
        ranked = weekly.rank_by_rules(options, GROUPS)
        cuisines = [r['cuisine'] for r in ranked]
        self.assertTrue(all(x != y for x, y in zip(cuisines, cuisines[1:])), cuisines)

    def test_alternation_gives_way_when_there_is_no_alternative(self):
        options = [recipe(f'r{i}', 'pollo', 1, cuisine='italiana') for i in range(3)]
        self.assertEqual(len(weekly.rank_by_rules(options, GROUPS)), 3)         # no se pierde ni se bloquea

    def test_empty_and_unknown_groups_do_not_break(self):
        self.assertEqual(weekly.rank_by_rules([], GROUPS), [])
        self.assertEqual(len(weekly.rank_by_rules([recipe('x', None, 99)], {})), 1)


class OfferFallbackTests(SimpleTestCase):
    """«Otra opción» en modo rules: `offer` decide el respaldo; volver a excluir `avoid_names` lo anularía."""

    def test_offer_fallback_must_not_be_filtered_again_by_avoid_names(self):
        from apps.plans.rules import recipes
        candidates = [dict(r, items=[{'food_id': 1, 'grams': 100}]) for r in
                      (recipe('a', 'pollo', 1, cuisine='x'), recipe('b', 'huevo', 2, cuisine='y'),
                       recipe('c', 'res', 3, cuisine='z'))]
        avoid = ['a', 'b', 'c']                                    # todas ya usadas en otras comidas del día
        offered = recipes.offer(candidates, 1, (), avoid)
        self.assertTrue(offered.options)                           # offer vuelve a la lista completa
        self.assertTrue(recipes.select_options(offered.options, 1, ()))              # lo que hace el job ahora
        self.assertFalse(recipes.select_options(offered.options, 1, (), avoid))      # lo que fallaba antes


class SettingValidationTests(SimpleTestCase):
    def test_only_rules_or_ai_are_valid(self):
        import importlib
        import os
        from unittest import mock
        from config.settings import base
        for value in ('rules', 'AI', ' Rules '):
            with self.subTest(value=value), mock.patch.dict(os.environ, {'NUTRITION_RANKING_MODE': value}):
                importlib.reload(base)
                self.assertIn(base.NUTRITION_RANKING_MODE, ('rules', 'ai'))
        with mock.patch.dict(os.environ, {'NUTRITION_RANKING_MODE': 'gemini'}):
            with self.assertRaises(ImproperlyConfigured):
                importlib.reload(base)
        importlib.reload(base)   # deja el módulo como estaba


@override_settings(NUTRITION_RANKING_MODE='rules')
class RulesModeFlowTests(NutritionScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.ready(self.client_a)

    def test_weekly_plan_is_generated_without_calling_the_ai(self):
        job, fake = self.request_nutrition(self.client_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        self.assertEqual(fake.calls, [])                                     # cero llamadas a Gemini
        plan = job.plan
        self.assertEqual((plan.status, plan.is_simulated), (Plan.STATUS_DRAFT_AI, False))
        self.assertEqual((plan.model_name, plan.prompt_version), (weekly.RULES_VERSION, weekly.RULES_VERSION))
        slots = plan.nutrition.slots.all()
        self.assertEqual(slots.count(), weekly.DAYS * 3)                     # 7 días x 3 comidas
        for slot in slots:
            self.assertEqual(slot.options.filter(is_active=True).count(), 3)

    def test_the_menu_passes_the_same_checks_as_the_ai_menu(self):
        job, _fake = self.request_nutrition(self.client_a)
        for slot in job.plan.nutrition.slots.all():
            names = list(slot.options.values_list('name', flat=True))
            self.assertEqual(len(set(names)), 3)                              # sin repetir dentro de la comida
            for option in slot.options.all():
                self.assertLessEqual(abs(option.kcal - slot.kcal_target), slot.kcal_target * 0.05)

    def test_no_dish_repeats_on_consecutive_days(self):
        job, _fake = self.request_nutrition(self.client_a)
        by_day = {}
        for slot in job.plan.nutrition.slots.all():
            by_day.setdefault((slot.code, slot.day_index), set()).update(slot.options.values_list('name', flat=True))
        for (code, day), names in by_day.items():
            if day > 1:
                self.assertFalse(names & by_day[(code, day - 1)], (code, day))

    def test_the_plan_is_not_presented_as_ai_generated(self):
        from django.core import mail
        from apps.plans import overview
        from apps.plans.models import PlanEvent
        mail.outbox.clear()
        job, _fake = self.request_nutrition(self.client_a)
        note = PlanEvent.objects.get(plan=job.plan, action=PlanEvent.ACTION_CREATED).note
        self.assertIn('por reglas', note)
        self.assertNotIn('con IA', note)
        job.plan.was_cloned = False      # lo anota `plan_groups` en producción
        self.assertEqual(overview._origin(job.plan, None)[1], 'Generado por reglas')
        self.assertTrue(all('con IA' not in m.body for m in mail.outbox))

    def test_the_approved_plan_can_be_approved_like_any_other(self):
        job, _fake = self.request_nutrition(self.client_a)
        approved = services.approve_plan(job.plan, self.boss, note_for_client='Buen provecho')
        self.assertEqual(approved.status, Plan.STATUS_APPROVED)

    def test_another_option_is_chosen_without_the_ai(self):
        plan = self.approved_nutrition(self.client_a)
        slot = plan.nutrition.slots.get(day_index=1, code='lunch')
        option = slot.options.get(position=2)
        names_before = list(slot.options.filter(is_active=True).values_list('name', flat=True))
        with self.fake_ai() as fake, self.captureOnCommitCallbacks(execute=True):
            job, _created = nutrition.request_alternative(
                self.client_a, slot.pk, option.pk, services.new_request_token())
        job.refresh_from_db()
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        self.assertEqual(fake.calls, [])
        new = slot.options.filter(is_active=True).order_by('position')[1]
        self.assertNotIn(new.name, names_before)
        self.assertEqual((new.origin, new.needs_review), ('client_regen', True))
        self.assertEqual(slot.options.filter(is_active=True).count(), 3)


@override_settings(NUTRITION_RANKING_MODE='ai')
class AiModeStillWorksTests(NutritionScenarioTestCase):
    """El modo anterior sigue disponible: con `ai` la IA ordena y se registra su modelo."""

    def test_ai_mode_calls_the_ai_once_per_meal(self):
        self.ready(self.client_a)
        job, fake = self.request_nutrition(self.client_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        self.assertEqual(len(fake.calls), 3)                                  # una por comida
        self.assertNotEqual(job.plan.model_name, weekly.RULES_VERSION)
        from apps.plans import overview
        job.plan.was_cloned = False
        self.assertEqual(overview._origin(job.plan, None)[1], 'Generado con IA')
