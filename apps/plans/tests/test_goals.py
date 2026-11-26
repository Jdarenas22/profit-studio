"""Orden de las recetas por objetivo, medidas y ficha (rules/goals.py) y cobertura mínima del recetario."""
import importlib.util
from decimal import Decimal as D
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from apps.assessments import services as assessment_services
from apps.plans.foods_data import food_rows
from apps.plans.rules import foods as rules_foods
from apps.plans.rules import goals, recipes, weekly
from apps.plans.rules import meal_validate as meal

from .helpers import NutritionScenarioTestCase


def food(number, name, kcal, protein, carbs, fat, fiber='1', sat=None, sodium=None, added=None, common=True,
         group='cereales'):
    return rules_foods.FoodItem(
        id=number, name=name, group=group, kcal100=D(str(kcal)), protein100=D(str(protein)), carbs100=D(str(carbs)),
        fat100=D(str(fat)), fiber100=None if fiber is None else D(str(fiber)),
        saturated_fat100=None if sat is None else D(str(sat)), sodium_mg100=None if sodium is None else D(str(sodium)),
        added_sugars100=None if added is None else D(str(added)), is_common=common)


# Alimentos con todos los datos para poder puntuar cada rasgo
CHICKEN = food(1, 'pollo', 165, 31, 0, 3.6, fiber='0', sat=1, sodium=70, added=0, group='proteina_animal')
BROCCOLI = food(2, 'brócoli', 35, 2.4, 7.2, 0.4, fiber='3.3', sat=0, sodium=30, added=0, group='verduras')
RICE = food(3, 'arroz', 130, 2.7, 28, 0.3, fiber='0.4', sat=0, sodium=1, added=0)
OIL = food(4, 'aceite', 884, 0, 0, 100, fiber='0', sat=14, sodium=0, added=0, group='grasas')
HONEY = food(5, 'miel', 304, 0.3, 82, 0, fiber='0', sat=0, sodium=4, added=82, group='azucares')
CHEESE = food(6, 'queso graso', 400, 24, 2, 33, fiber='0', sat=21, sodium=700, added=0, group='lacteos')
OATS = food(7, 'avena', 380, 13, 67, 7, fiber='10', sat=1.2, sodium=5, added=0)
BANANA = food(8, 'banano', 89, 1.1, 22.8, 0.3, fiber='2.6', sat=0.1, sodium=1, added=0, group='frutas')
RARE = food(9, 'ingrediente raro', 100, 5, 10, 4, fiber='1', sat=1, sodium=10, added=0, common=False)
FOODS = {f.id: f for f in (CHICKEN, BROCCOLI, RICE, OIL, HONEY, CHEESE, OATS, BANANA, RARE)}


def dish(*pairs):
    return [{'food_id': food_id, 'grams': grams} for food_id, grams in pairs]


LIGHT_PROTEIN = dish((1, 150), (2, 200), (4, 5))                    # pollo + brócoli: proteico, con fibra y ligero
SUGARY_DENSE = dish((5, 40), (7, 60), (4, 15))                      # miel + avena + aceite: denso y con azúcar
CARB_RICH = dish((3, 250), (1, 60), (8, 100))                       # arroz + poco pollo + banano: muchos carbohidratos
FATTY = dish((6, 80), (4, 15), (3, 40))                             # queso graso + aceite: grasa saturada y sodio


class MetricsTests(SimpleTestCase):
    def test_shares_are_computed_from_the_portions(self):
        metrics = goals.recipe_metrics(dish((1, 100), (3, 100)), FOODS)       # 165+130 kcal
        self.assertAlmostEqual(metrics['protein_share'], (31 + 2.7) * 4 / 295, places=4)
        self.assertAlmostEqual(metrics['carb_share'], 28 * 4 / 295, places=4)
        self.assertAlmostEqual(metrics['energy_density'], 295 / 200 * 100, places=4)
        self.assertEqual(metrics['common_share'], 1.0)

    def test_a_missing_nutrient_is_unknown_not_zero(self):
        foods = {1: food(1, 'x', 100, 10, 10, 2, fiber=None, sat=None, sodium=None, added=None)}
        metrics = goals.recipe_metrics(dish((1, 100)), foods)
        for feature in ('fiber_density', 'sat_fat_share', 'sodium_density', 'added_sugars'):
            self.assertIsNone(metrics[feature])
        self.assertIsNotNone(metrics['protein_share'])

    def test_unknown_food_or_empty_dish_gives_no_metrics(self):
        self.assertIsNone(goals.recipe_metrics(dish((99, 100)), FOODS))
        self.assertIsNone(goals.recipe_metrics([], FOODS))

    def test_bmi(self):
        self.assertAlmostEqual(float(goals.bmi_of('82', '1.64')), 82 / 1.64 ** 2, places=4)
        for bad in ((None, '1.6'), ('70', None), ('0', '1.6'), ('70', '0'), ('x', '1.6')):
            self.assertIsNone(goals.bmi_of(*bad))


class GoalScoringTests(SimpleTestCase):
    def score(self, items, **context):
        return goals.score_recipe(goals.recipe_metrics(items, FOODS), goals.GoalContext(**context))

    def test_every_goal_prefers_its_own_kind_of_dish(self):
        cases = {
            goals.GOAL_WEIGHT_LOSS: (LIGHT_PROTEIN, SUGARY_DENSE),
            goals.GOAL_TONING: (LIGHT_PROTEIN, SUGARY_DENSE),
            goals.GOAL_SPORTS: (CARB_RICH, FATTY),
            goals.GOAL_CARDIO: (CARB_RICH, FATTY),
            goals.GOAL_REHAB: (LIGHT_PROTEIN, FATTY),
            goals.GOAL_MUSCLE_GAIN: (LIGHT_PROTEIN, SUGARY_DENSE),
        }
        for goal, (better, worse) in cases.items():
            with self.subTest(goal=goal):
                self.assertGreater(self.score(better, goal=goal), self.score(worse, goal=goal))

    def test_the_same_dishes_swap_order_between_opposite_goals(self):
        # Para correr se prefiere el plato de carbohidratos; para bajar de peso, el ligero y proteico
        self.assertGreater(self.score(CARB_RICH, goal=goals.GOAL_SPORTS), self.score(LIGHT_PROTEIN, goal=goals.GOAL_SPORTS))
        self.assertGreater(self.score(LIGHT_PROTEIN, goal=goals.GOAL_WEIGHT_LOSS),
                           self.score(CARB_RICH, goal=goals.GOAL_WEIGHT_LOSS))

    def test_scores_stay_between_minus_one_and_one(self):
        for goal in goals.GOAL_PROFILES:
            for items in (LIGHT_PROTEIN, SUGARY_DENSE, CARB_RICH, FATTY):
                for bmi in (None, D('17'), D('24'), D('35')):
                    value = self.score(items, goal=goal, bmi=bmi, budget='low')
                    self.assertGreaterEqual(value, -1.0)
                    self.assertLessEqual(value, 1.0)

    def test_no_goal_and_no_low_budget_means_a_neutral_order(self):
        options = [{'name': 'a', 'items': LIGHT_PROTEIN}, {'name': 'b', 'items': FATTY}]
        self.assertEqual(goals.score_options(options, FOODS, goals.GoalContext()), {})
        self.assertEqual(goals.score_options(options, FOODS, goals.GoalContext(goal='desconocida')), {})
        self.assertEqual(goals.score_options(options, FOODS, None), {})

    def test_a_low_budget_prefers_common_ingredients(self):
        common, rare = dish((3, 150), (1, 100)), dish((9, 150), (1, 100))
        self.assertGreater(self.score(common, budget='low'), self.score(rare, budget='low'))
        self.assertEqual(self.score(common, budget='medium'), 0.0)       # sin objetivo ni presupuesto bajo: neutro

    def test_a_high_bmi_strengthens_satiety_and_a_low_bmi_stops_rewarding_light_dishes(self):
        normal = goals._weights(goals.GoalContext(goal=goals.GOAL_WEIGHT_LOSS, bmi=D('24')))
        high = goals._weights(goals.GoalContext(goal=goals.GOAL_WEIGHT_LOSS, bmi=D('33')))
        self.assertAlmostEqual(high['fiber_density'], normal['fiber_density'] * goals.BMI_SATIETY_BOOST)
        self.assertAlmostEqual(high['energy_density'], normal['energy_density'] * goals.BMI_SATIETY_BOOST)
        low = goals._weights(goals.GoalContext(goal=goals.GOAL_WEIGHT_LOSS, bmi=D('17.5')))
        self.assertGreater(low['energy_density'], 0)                     # con bajo peso no se premia lo ligero
        # el boost no se aplica a quien busca masa o rendimiento
        for goal in (goals.GOAL_MUSCLE_GAIN, goals.GOAL_SPORTS):
            plain = goals._weights(goals.GoalContext(goal=goal, bmi=D('24')))
            heavy = goals._weights(goals.GoalContext(goal=goal, bmi=D('33')))
            self.assertEqual(plain, heavy)

    def test_unknown_nutrients_are_skipped_instead_of_punished(self):
        foods = {1: food(1, 'x', 165, 31, 0, 3.6, fiber=None, sat=None, sodium=None, added=None)}
        value = goals.score_recipe(goals.recipe_metrics(dish((1, 150)), foods),
                                   goals.GoalContext(goal=goals.GOAL_CARDIO))     # solo carb_share cuenta
        self.assertGreaterEqual(value, -1.0)
        self.assertLessEqual(value, 1.0)


def catalog_items():
    return {number: rules_foods.FoodItem(
        id=number, name=row['name'], group=row['group'], kcal100=row['kcal100'], protein100=row['protein100'],
        carbs100=row['carbs100'], fat100=row['fat100'], fiber100=row.get('fiber100'),
        allergen_tags=tuple(row['allergen_tags']), trace_tags=tuple(row['trace_tags']),
        diet_tags=tuple(row['diet_tags']), condition_tags=tuple(row['condition_tags']),
        is_common=row.get('is_common', True)) for number, row in enumerate(food_rows(), 1)}


class RealRecipesOrderTests(SimpleTestCase):
    """Con las recetas y los alimentos reales: el orden cambia de verdad con el objetivo (semilla fija, determinista)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.foods = catalog_items()
        rules = meal.SlotRules(slot_code='lunch', kcal=D('640'), protein_g=D('40'), foods=cls.foods)
        cls.options = recipes.candidates(rules, 'basic', seed='semilla-fija')
        cls.groups = {fid: f.group for fid, f in cls.foods.items()}

    def top_mean(self, feature, context, n=10):
        scores = goals.score_options(self.options, self.foods, context)
        ranked = weekly.rank_by_rules(list(self.options), self.groups, scores)[:n]
        values = [goals.recipe_metrics(o['items'], self.foods)[feature] for o in ranked]
        return sum(values) / len(values)

    def test_there_are_enough_candidates_to_compare(self):
        self.assertGreaterEqual(len(self.options), 20)

    def test_sports_pushes_carbohydrate_rich_dishes_up(self):
        neutral = self.top_mean('carb_share', goals.GoalContext())
        sports = self.top_mean('carb_share', goals.GoalContext(goal=goals.GOAL_SPORTS))
        self.assertGreater(sports, neutral)

    def test_weight_loss_pushes_protein_and_lighter_dishes_up(self):
        neutral = self.top_mean('protein_share', goals.GoalContext())
        loss = self.top_mean('protein_share', goals.GoalContext(goal=goals.GOAL_WEIGHT_LOSS, bmi=D('31')))
        self.assertGreater(loss, neutral)
        self.assertLess(self.top_mean('energy_density', goals.GoalContext(goal=goals.GOAL_WEIGHT_LOSS, bmi=D('31'))),
                        self.top_mean('energy_density', goals.GoalContext()))

    def test_the_ranking_keeps_every_recipe_and_is_deterministic(self):
        context = goals.GoalContext(goal=goals.GOAL_CARDIO, bmi=D('27'), budget='low')
        scores = goals.score_options(self.options, self.foods, context)
        first = weekly.rank_by_rules(list(self.options), self.groups, scores)
        second = weekly.rank_by_rules(list(self.options), self.groups, scores)
        self.assertEqual([o['name'] for o in first], [o['name'] for o in second])
        self.assertEqual(sorted(o['name'] for o in first), sorted(o['name'] for o in self.options))

    def test_the_top_is_still_varied_in_main_protein(self):
        scores = goals.score_options(self.options, self.foods, goals.GoalContext(goal=goals.GOAL_MUSCLE_GAIN))
        ranked = weekly.rank_by_rules(list(self.options), self.groups, scores)[:6]
        self.assertGreaterEqual(len({o['main_protein'] for o in ranked}), 4)


class JobUsesTheClientsContextTests(NutritionScenarioTestCase):
    def test_the_nutrition_job_scores_recipes_with_goal_bmi_and_budget(self):
        client = self.client_a
        self.ready(client, budget_level='low', training_goal='weight_loss')      # el objetivo llega desde la ficha
        client.refresh_from_db()
        assessment_services.create_member_measurement(
            client, {'weight': D('82'), 'waist_cm': D('90'), 'hip_cm': D('104')}, D('1.64'))
        seen = []
        original = goals.score_options

        def spy(options, foods, context):
            seen.append(context)
            return original(options, foods, context)
        with override_settings(NUTRITION_RANKING_MODE='rules'), mock.patch('apps.plans.jobs.rules_goals.score_options', spy):
            job, fake = self.request_nutrition(client)
        self.assertEqual(job.status, job.STATUS_SUCCEEDED, job.error_code)
        self.assertEqual(fake.calls, [])                                    # sin IA
        self.assertTrue(seen)
        context = seen[0]
        self.assertEqual(context.goal, 'weight_loss')
        self.assertEqual(context.budget, 'low')
        self.assertAlmostEqual(float(context.bmi), 82 / 1.64 ** 2, places=3)


class RecipeCoverageGuardTests(SimpleTestCase):
    """El recetario debe seguir ofreciendo variedad a los perfiles más restrictivos (medidor: scripts/recipe_coverage.py)."""
    MINIMUM = 8        # el menú semanal exige 6 distintas por comida; con menos de 8 casi no hay variedad entre semanas

    def test_the_most_restrictive_profiles_keep_enough_recipes(self):
        spec = importlib.util.spec_from_file_location(
            'recipe_coverage', Path(settings.BASE_DIR) / 'scripts' / 'recipe_coverage.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        foods = module.production_foods()
        targets = [module.slot_targets(client) for client in module.CLIENTS]
        weak = []
        for label, diet, allergies in module.PROFILES:
            for slot in module.SLOTS:
                union = set()
                for target in targets:
                    union |= module.usable_recipes(foods, target, diet, allergies, slot)
                if len(union) < self.MINIMUM:
                    weak.append(f'{label}/{slot}={len(union)}')
        self.assertEqual(weak, [], 'Perfiles con pocas recetas (añade recetas o revisa los alimentos): ' + ', '.join(weak))
