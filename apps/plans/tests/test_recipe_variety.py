"""Variedad del recetario (everyday-world/2): catálogo ampliado, orden sin sesgo por trabajo, nombres
que no se repiten dentro del día (con respaldo) y proteína principal distinta entre las opciones de
una comida. Todo sin red: simulador (`FakeAIClient`) y recetario real.
"""
from dataclasses import replace
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase

from apps.plans import nutrition, services
from apps.plans.ai import prompts
from apps.plans.ai.fake import FakeAIClient, sample_meal_slot
from apps.plans.foods_data import food_rows
from apps.plans.models import KIND_NUTRITION, MealOption, PlanGenerationJob
from apps.plans.rules import energy
from apps.plans.rules import foods as rules_foods
from apps.plans.rules import meal_validate as meal
from apps.plans.rules import recipes
from apps.plans.rules.validate import check_text

from .helpers import NutritionScenarioTestCase
from .test_wellness_recipes import rules_for

FIVE_MEALS = ['breakfast', 'mid_morning', 'lunch', 'snack', 'dinner']

# Alimentos del catálogo que ninguna receta usaba en everyday-world/1
FORMERLY_UNUSED = (
    'Camarón cocido', 'Lomo de cerdo magro cocido', 'Pechuga de pavo cocida', 'Muslo de pollo cocido sin piel',
    'Carne molida de res magra cocida', 'Fríjol negro cocido', 'Arveja verde cocida', 'Choclo (maíz tierno) cocido',
    'Champiñones', 'Pimentón', 'Cebolla cabezona', 'Habichuela cocida', 'Coliflor cocida', 'Ahuyama cocida',
    'Arracacha cocida', 'Papa criolla cocida', 'Remolacha cocida', 'Repollo crudo', 'Ñame cocido',
    'Plátano verde cocido', 'Clara de huevo cocida', 'Kiwi', 'Mora', 'Naranja', 'Mandarina', 'Uvas', 'Sandía',
    'Leche descremada', 'Leche semidescremada', 'Yogur natural sin azúcar', 'Linaza molida',
    'Pan blanco tajado', 'Galletas de soda',
)


def entry(name, protein, *food_ids):
    return {'name': name, 'cuisine': 'prueba', 'main_protein': protein, 'preparation': 'Sirve.',
            'items': [{'food_id': food_id, 'grams': 100} for food_id in food_ids]}


def names(options):
    return [option['name'] for option in options]


# ─── El catálogo ──────────────────────────────────────────────────────────────

class CatalogTests(SimpleTestCase):
    def test_version_and_unique_names(self):
        self.assertEqual(recipes.VERSION, 'everyday-world/3')
        lowered = [recipe[0].casefold() for recipe in recipes.RECIPES]
        self.assertEqual(len(lowered), len(set(lowered)))

    def test_every_recipe_uses_exact_catalog_foods_valid_slots_and_clean_text(self):
        catalog = {row['name'] for row in food_rows()}
        for name, cuisine, slots, preparation, ingredients, cooking, family in recipes.RECIPES:
            with self.subTest(recipe=name):
                self.assertTrue(slots and set(slots) <= set(energy.SLOT_ORDER))
                self.assertIn(cooking, ('basic', 'none'))
                foods = [food for food, _low, _high in ingredients]
                self.assertLessEqual(set(foods), catalog)                      # nombres exactos de foods_data.py
                self.assertEqual(len(foods), len(set(foods)))
                self.assertTrue(meal.MIN_ITEMS <= len(foods) <= meal.MAX_ITEMS)
                for food, low, high in ingredients:
                    self.assertTrue(meal.MIN_GRAMS <= low <= high <= meal.MAX_GRAMS, food)
                    self.assertEqual((low % 5, high % 5), (0, 0), food)        # los gramos se escogen de 5 en 5
                self.assertTrue(cuisine)
                self.assertTrue(family, 'toda receta declara su proteína principal')
                errors = []
                check_text(name, meal.MAX_NAME, 'nombre', errors, required=True)
                check_text(preparation, meal.MAX_PREPARATION, 'preparación', errors, required=True)
                self.assertEqual(errors, [])                                   # sin suplementos, dietas ni enlaces
                self.assertFalse(any(ch.isdigit() for ch in preparation), 'sin cantidades ni calorías')

    def test_no_offered_recipe_names_an_allergen_or_a_food_the_diet_excludes(self):
        """La segunda capa de la validación (V03) revisa nombre y preparación de lo que elige la IA: un
        plato permitido jamás debe tropezar con ella (p. ej. "mantequilla de maní" no es lácteo)."""
        items = [rules_foods.FoodItem(
            id=number, name=row['name'], group=row['group'], kcal100=row['kcal100'], protein100=row['protein100'],
            carbs100=row['carbs100'], fat100=row['fat100'], allergen_tags=tuple(row['allergen_tags']),
            trace_tags=tuple(row['trace_tags']), diet_tags=tuple(row['diet_tags']),
            condition_tags=tuple(row['condition_tags'])) for number, row in enumerate(food_rows(), 1)]
        for diet in rules_foods.DIET_CODES:
            for allergen in ('',) + rules_foods.ALLERGEN_CODES:
                allergies = (allergen,) if allergen else ()
                allowed = rules_foods.filter_foods(items, rules_foods.NutritionProfile(diet=diet, allergies=allergies))
                available = {food.name for food in allowed.allowed}
                safe = meal.safe_phrases(allowed.allowed)
                for name, _cuisine, _slots, preparation, ingredients, _cooking, _family in recipes.RECIPES:
                    if all(food in available for food, _low, _high in ingredients):
                        with self.subTest(diet=diet, allergen=allergen, recipe=name):
                            self.assertEqual(
                                meal.forbidden_in_text(f'{name} {preparation}', allergies, diet, safe), [])

    def test_main_protein_defaults_to_the_first_protein_food_and_can_be_explicit(self):
        self.assertEqual(recipes.protein_of('Pollo con arroz y ensalada'), 'pollo')
        self.assertEqual(recipes.protein_of('Arroz con camarones y pimentón'), 'camarón')
        self.assertEqual(recipes.protein_of('Pan con hummus y huevo'), 'huevo')          # explícita
        self.assertEqual(recipes.protein_of('Avena con yogur griego, banano y maní'), 'yogur')
        self.assertEqual(recipes.protein_of('LOMO DE CERDO CON ARRACACHA Y HABICHUELA'), 'cerdo')
        self.assertIsNone(recipes.protein_of('Plato inventado'))
        self.assertIsNone(recipes.protein_of(None))

    def test_recipe_counts_grew_for_every_meal_time(self):
        def count(slot):
            return sum(1 for recipe in recipes.RECIPES if slot in recipe[2])
        # everyday-world/1 tenía 13 de desayuno, 19 de media mañana/merienda y 29 de almuerzo/cena
        self.assertGreaterEqual(count('breakfast'), 13 + 8)
        self.assertGreaterEqual(count('mid_morning'), 19 + 8)
        self.assertGreaterEqual(count('snack'), 19 + 8)
        self.assertGreaterEqual(count('lunch'), 29 + 20)
        self.assertGreaterEqual(count('dinner'), 29 + 20)
        self.assertGreater(len(recipes.RECIPES), 52)

    def test_foods_no_recipe_used_before_are_now_used(self):
        used = {food for recipe in recipes.RECIPES for food, _low, _high in recipe[4]}
        self.assertEqual([food for food in FORMERLY_UNUSED if food not in used], [])

    def test_every_meal_time_has_recipes_for_someone_who_does_not_cook(self):
        for slot in energy.SLOT_ORDER:
            with self.subTest(slot=slot):
                cold = [recipe for recipe in recipes.RECIPES if slot in recipe[2] and recipe[5] == 'none']
                self.assertGreaterEqual(len(cold), 3)
        for slot in ('lunch', 'dinner'):        # antes no quedaba ninguna con un objetivo alto de proteína
            rules = replace(rules_for(slot), kcal=Decimal('570'), protein_g=Decimal('39'))
            self.assertGreaterEqual(len(recipes.candidates(rules, cooking='none')), 3)

    def test_more_proteins_and_sides_are_offered_at_lunch(self):
        proteins = {option['main_protein'] for option in recipes.candidates(rules_for('lunch'))}
        self.assertGreaterEqual(proteins, {'pollo', 'res', 'cerdo', 'pavo', 'tilapia', 'salmón', 'atún', 'camarón',
                                           'huevo', 'tofu', 'lentejas', 'fríjol', 'garbanzo'})


# ─── Orden sin sesgo ──────────────────────────────────────────────────────────

class OrderTests(SimpleTestCase):
    def test_order_is_reproducible_for_the_same_seed_and_slot(self):
        rules = rules_for('lunch')
        first = names(recipes.candidates(rules, seed='trabajo-1'))
        self.assertEqual(first, names(recipes.candidates(rules, seed='trabajo-1')))
        self.assertNotEqual(first, names(recipes.candidates(rules, seed='trabajo-2')))
        self.assertNotEqual(first, names(recipes.candidates(rules_for('dinner'), seed='trabajo-1')))

    def test_without_a_seed_the_catalog_order_is_kept(self):
        listed = names(recipes.candidates(rules_for('lunch')))
        order = {recipe[0]: index for index, recipe in enumerate(recipes.RECIPES)}
        self.assertEqual(listed, sorted(listed, key=order.get))

    def test_the_seed_only_reorders_never_changes_recipes_or_portions(self):
        rules = rules_for('lunch')
        plain = {option['name']: option for option in recipes.candidates(rules)}
        shuffled = {option['name']: option for option in recipes.candidates(rules, seed='otro')}
        self.assertEqual(plain, shuffled)

    def test_the_first_recipe_is_not_always_chicken(self):
        rules = rules_for('lunch')
        firsts = [recipes.candidates(rules, seed=f'trabajo-{number}')[0]['main_protein'] for number in range(40)]
        self.assertGreater(len(set(firsts)), 4)
        self.assertLess(firsts.count('pollo'), 15)


# ─── Selección y oferta ───────────────────────────────────────────────────────

class SelectionTests(SimpleTestCase):
    def setUp(self):
        self.pool = [entry('P1', 'pollo', 1, 2), entry('P2', 'pollo', 3, 4), entry('P3', 'pollo', 5, 6),
                     entry('R1', 'res', 7, 8), entry('T1', 'tilapia', 9, 10)]

    def test_prefers_a_group_without_a_repeated_main_protein(self):
        self.assertEqual(names(recipes.select_options(self.pool, 3)), ['P1', 'R1', 'T1'])
        self.assertEqual(names(recipes.select_options(self.pool, 3, protein='ignore')), ['P1', 'P2', 'P3'])

    def test_falls_back_to_the_previous_rule_when_no_group_has_distinct_proteins(self):
        pool = self.pool[:4]                            # tres de pollo y una de res: no hay tres proteínas
        self.assertEqual(names(recipes.select_options(pool, 3)), ['P1', 'P2', 'P3'])
        self.assertEqual(recipes.select_options(pool, 3, protein='require'), [])
        self.assertEqual(recipes.select_options(self.pool[:2], 3), [])

    def test_returned_options_hide_the_selection_only_keys(self):
        for option in recipes.select_options(self.pool, 3):
            self.assertEqual(set(option), {'name', 'preparation', 'items'})

    def test_avoid_names_and_existing_options_are_never_returned(self):
        existing = (meal.ExistingOption(name='r1', food_ids=frozenset({7, 8})),)
        group = recipes.select_options(self.pool, 2, existing=existing, avoid_names=['p1'])
        self.assertNotIn('R1', names(group))
        self.assertNotIn('P1', names(group))

    def test_similar_recipes_are_not_grouped(self):
        pool = [entry('A', 'pollo', 1, 2, 3), entry('B', 'res', 1, 2, 4), entry('C', 'tilapia', 9, 10, 11)]
        self.assertEqual(names(recipes.select_options(pool, 2)), ['A', 'C'])

    def test_a_replacement_prefers_a_protein_the_meal_does_not_have_yet(self):
        existing = tuple(meal.ExistingOption(name=name, food_ids=frozenset({index * 10 + 1, index * 10 + 2}))
                         for index, name in enumerate(('Pollo con arroz y ensalada', 'Tofu con arroz y ensalada',
                                                       'Res magra con arroz y ensalada')))
        pool = [entry('Otro pollo', 'pollo', 91, 92), entry('Otra res', 'res', 93, 94),
                entry('Camarones', 'camarón', 95, 96)]
        self.assertEqual(names(recipes.select_options(pool, 1, existing=existing)), ['Camarones'])
        only_taken = pool[:2]               # sin otra proteína, se usa la regla de siempre
        self.assertEqual(names(recipes.select_options(only_taken, 1, existing=existing)), ['Otro pollo'])


class OfferTests(SimpleTestCase):
    def setUp(self):
        self.pool = [entry('P1', 'pollo', 1, 2), entry('P2', 'pollo', 3, 4), entry('R1', 'res', 5, 6),
                     entry('T1', 'tilapia', 7, 8), entry('C1', 'camarón', 9, 10), entry('Z1', 'cerdo', 11, 12)]

    def test_used_names_leave_the_offered_list_and_a_distinct_group_is_required(self):
        offered = recipes.offer(self.pool, 3, avoid_names=['P1', 'r1'])
        self.assertEqual(names(offered.options), ['P2', 'T1', 'C1', 'Z1'])
        self.assertTrue(offered.distinct_protein)

    def test_falls_back_to_the_full_list_when_the_exclusion_leaves_no_valid_group(self):
        offered = recipes.offer(self.pool[:4], 3, avoid_names=['P1'])         # sin P1 quedan 3 y forman grupo
        self.assertEqual((names(offered.options), offered.distinct_protein), (['P2', 'R1', 'T1'], True))
        offered = recipes.offer(self.pool[:3], 3, avoid_names=['P1'])         # sin P1 quedan 2: no hay grupo
        self.assertEqual(names(offered.options), ['P1', 'P2', 'R1'])
        self.assertFalse(offered.distinct_protein)                            # P1 y P2 son de pollo

    def test_nothing_is_offered_when_even_the_full_list_cannot_make_a_group(self):
        offered = recipes.offer(self.pool[:2], 3)
        self.assertEqual((offered.options, offered.distinct_protein), ([], False))

    def test_options_of_the_meal_and_similar_ones_are_not_offered(self):
        existing = (meal.ExistingOption(name='P1', food_ids=frozenset({1, 2})),)
        pool = self.pool + [entry('Parecida', 'atún', 1, 2, 20)]
        offered = recipes.offer(pool, 1, existing=existing)
        self.assertNotIn('P1', names(offered.options))
        self.assertNotIn('Parecida', names(offered.options))                  # comparte 2 de 3 alimentos con P1

    def test_a_replacement_is_offered_only_other_proteins_when_there_are_some(self):
        existing = (meal.ExistingOption(name='Pollo con arroz y ensalada', food_ids=frozenset({101, 102})),)
        pool = [entry('Otro pollo', 'pollo', 1, 2), entry('Res', 'res', 3, 4)]
        offered = recipes.offer(pool, 1, existing=existing)
        self.assertEqual((names(offered.options), offered.distinct_protein), (['Res'], True))
        offered = recipes.offer(pool[:1], 1, existing=existing)               # solo queda pollo: se ofrece igual
        self.assertEqual((names(offered.options), offered.distinct_protein), (['Otro pollo'], False))

    def test_a_long_list_is_capped_keeping_the_order_and_every_protein(self):
        families = [('pollo', 60), ('res', 3), ('tilapia', 3), ('camarón', 4)]
        pool = [entry(f'{family} {number}', family, len(family) * 100 + number * 2, len(family) * 100 + number * 2 + 1)
                for family, amount in families for number in range(amount)]
        pool = [pool[index] for index in (list(range(0, 60, 2)) + list(range(1, 60, 2)) + list(range(60, 70)))]
        offered = recipes.offer(pool, 3)
        self.assertEqual(len(offered.options), recipes.MAX_OFFERED)
        self.assertTrue(offered.distinct_protein)
        kept = names(offered.options)
        self.assertEqual(kept, [name for name in names(pool) if name in set(kept)])            # mismo orden
        self.assertEqual({option['main_protein'] for option in offered.options}, {'pollo', 'res', 'tilapia', 'camarón'})
        self.assertTrue(recipes.select_options(offered.options, 3, protein='require'))

    def test_a_short_list_is_not_capped(self):
        self.assertEqual(len(recipes.offer(self.pool, 3).options), len(self.pool))

    def test_the_offer_keeps_the_given_order(self):
        offered = recipes.offer(list(reversed(self.pool)), 3)
        self.assertEqual(names(offered.options), list(reversed(['P1', 'P2', 'R1', 'T1', 'C1', 'Z1'])))


class ProteinValidationTests(SimpleTestCase):
    """El validador exige proteínas distintas solo cuando existe un grupo así (`distinct_protein`)."""

    def setUp(self):
        base = rules_for('lunch')
        self.offered = recipes.candidates(base)
        self.rules = replace(base, recipes=tuple(self.offered))
        self.by_name = {option['name']: option for option in self.offered}

    def data(self, *recipe_names):
        return {'schema_version': meal.MEAL_SCHEMA_VERSION, 'slot_code': 'lunch', 'options': [
            {'name': name, 'items': self.by_name[name]['items'], 'preparation': self.by_name[name]['preparation']}
            for name in recipe_names]}

    SAME = ('Pollo con arroz y ensalada', 'Bowl de pollo con quinua', 'Pollo con papa y brócoli')   # el caso medido
    VARIED = ('Pollo con arroz y ensalada', 'Bowl de pollo con quinua', 'Salmón con batata y espinaca')

    def test_three_chicken_dishes_are_rejected_when_a_varied_group_exists(self):
        strict = replace(self.rules, distinct_protein=True)
        result = meal.validate_meal_slot(self.data(*self.SAME), strict)
        self.assertEqual(result.codes, (meal.V06_VARIETY,))
        result = meal.validate_meal_slot(self.data(*self.VARIED), strict)
        self.assertEqual(result.codes, (meal.V06_VARIETY,))                   # dos de pollo todavía repiten
        varied = ('Pollo con arroz y ensalada', 'Salmón con batata y espinaca', 'Res magra con yuca y calabacín')
        self.assertTrue(meal.validate_meal_slot(self.data(*varied), strict).ok)

    def test_without_the_requirement_the_previous_rules_apply(self):
        self.assertFalse(self.rules.distinct_protein)
        self.assertTrue(meal.validate_meal_slot(self.data(*self.SAME), self.rules).ok)


# ─── El flujo completo ────────────────────────────────────────────────────────

def plan_names(plan):
    result = {}
    for slot in plan.nutrition.slots.filter(day_index=1):
        result[slot.code] = list(slot.options.filter(is_active=True).order_by('position').values_list('name', flat=True))
    return result


def name_enum(call):
    return call['schema']['properties']['options']['items']['properties']['name']['enum']


class VarietyFlowTests(NutritionScenarioTestCase):
    def five_meals(self, client, **overrides):
        self.ready(client, meal_slots=FIVE_MEALS, **overrides)
        job, fake = self.request_nutrition(client)
        self.assertEqual(job.status, job.STATUS_SUCCEEDED, job.error_code)
        return job, fake

    def test_lunch_and_dinner_do_not_share_dishes_for_any_goal(self):
        for goal in ('weight_loss', 'muscle_gain', 'toning'):
            with self.subTest(goal=goal):
                PlanGenerationJob.objects.all().delete()
                self.client_a.training_goal = goal
                self.client_a.save(update_fields=['training_goal'])
                job, _fake = self.five_meals(self.client_a, training_goal=goal)
                dishes = plan_names(job.plan)
                self.assertEqual(list(dishes), FIVE_MEALS)
                self.assertFalse(set(dishes['lunch']) & set(dishes['dinner']), dishes)
                everything = [name for options in dishes.values() for name in options]
                self.assertEqual(len(everything), len(set(everything)), dishes)       # ninguno se repite en el día

    def test_the_three_options_of_a_meal_do_not_share_the_main_protein(self):
        job, _fake = self.five_meals(self.client_a)
        for code, dishes in plan_names(job.plan).items():
            with self.subTest(meal=code):
                proteins = [recipes.protein_of(name) for name in dishes]
                self.assertTrue(all(proteins), dishes)
                self.assertEqual(len(set(proteins)), 3, dishes)

    def test_names_used_earlier_in_the_day_leave_the_list_and_the_schema(self):
        job, fake = self.five_meals(self.client_a)
        for day in range(1, 8):
            used = set()
            for slot in job.plan.nutrition.slots.filter(day_index=day):
                dishes = set(slot.options.values_list('name', flat=True))
                self.assertFalse(used & dishes)
                used.update(dishes)
        self.assertEqual(len(fake.calls), 5)
        for call in fake.calls:
            offered = names(call['payload']['recipe_options'])
            self.assertLessEqual(len(offered), recipes.MAX_OFFERED)
            self.assertEqual(call['schema']['properties']['recipe_names']['maxItems'], len(offered))

    def test_each_meal_gets_its_own_order_and_every_job_a_different_one(self):
        job, fake = self.five_meals(self.client_a)
        lunch = names(fake.calls[2]['payload']['recipe_options'])
        dinner = names(fake.calls[4]['payload']['recipe_options'])
        shared = set(lunch) & set(dinner)
        self.assertGreater(len(shared), 10)
        # las mismas recetas aparecen en otro orden en el almuerzo y en la cena
        self.assertNotEqual([name for name in lunch if name in shared], [name for name in dinner if name in shared])
        PlanGenerationJob.objects.all().delete()
        second, fake_two = self.request_nutrition(self.client_a, actor=self.boss)
        self.assertEqual(second.status, second.STATUS_SUCCEEDED, second.error_code)
        self.assertEqual(len(fake_two.calls), 5)
        self.assertNotEqual(lunch, names(fake_two.calls[2]['payload']['recipe_options']))   # otro trabajo, otro orden

    def test_payload_carries_the_main_protein_and_the_new_catalog_version(self):
        _job, fake = self.five_meals(self.client_a)
        for call in fake.calls:
            payload = call['payload']
            self.assertEqual(payload['schema_version'], 'weekly-menu/1')
            self.assertTrue(all(option['main_protein'] for option in payload['recipe_options']))
            self.assertEqual(set(payload['recipe_options'][0]), {'name', 'cuisine', 'main_protein', 'preparation'})
        self.assertIn('main_protein', prompts.MEAL_SYSTEM)
        self.assertEqual(prompts.meal_prompt(False)[1], 'meal/5')

    def test_a_model_that_repeats_a_recipe_is_rejected_once_and_then_corrected(self):
        class RepeatedNameFirst(FakeAIClient):
            def generate_json(self, **kwargs):
                result = super().generate_json(**kwargs)
                if not kwargs['payload']['previous_errors']:
                    bad = list(result.data['recipe_names'])
                    bad[-1] = bad[0]
                    return replace(result, data={'recipe_names': bad})
                return result
        self.ready(self.client_a)
        fake = RepeatedNameFirst()
        with mock.patch('apps.plans.ai.get_ai_client', return_value=fake), self.captureOnCommitCallbacks(execute=True):
            job, _ = services.request_plan(self.client_a, KIND_NUTRITION, self.client_a, services.new_request_token())
        job.refresh_from_db()
        self.assertEqual(job.status, job.STATUS_SUCCEEDED, job.error_code)
        self.assertEqual(fake.calls[1]['payload']['previous_errors'], ['V01_SCHEMA'])
        self.assertEqual(job.plan.nutrition.duration_days, 7)

    def test_with_very_few_recipes_weekly_variety_fails_before_spending_quota(self):
        keep = {'Pollo con arroz y ensalada', 'Bowl de res magra con quinua', 'Pescado con papa y brócoli',
                'Arepa con huevo y aguacate', 'Calentado de arroz, fríjol y huevo', 'Tostadas con huevo y tomate'}
        self.ready(self.client_a)
        with mock.patch.object(recipes, 'RECIPES', [recipe for recipe in recipes.RECIPES if recipe[0] in keep]):
            job, fake = self.request_nutrition(self.client_a)
        self.assertEqual(job.status, job.STATUS_FAILED)
        self.assertEqual(job.error_code, 'weekly_variety_unavailable')
        self.assertEqual(fake.calls, [])

    def test_unavailable_recipes_still_stop_the_job_before_calling_the_ai(self):
        self.ready(self.client_a)
        with mock.patch.object(recipes, 'RECIPES', []):
            job, fake = self.request_nutrition(self.client_a)
        self.assertEqual((job.status, job.error_code), (PlanGenerationJob.STATUS_FAILED, 'weekly_variety_unavailable'))
        self.assertEqual(fake.calls, [])


class AlternativeVarietyTests(NutritionScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.plan = self.approved_nutrition(self.client_a)
        self.slot = self.plan.nutrition.slots.get(code='lunch')

    def alternative(self, option):
        with self.fake_ai() as fake, self.captureOnCommitCallbacks(execute=True):
            job, _created = nutrition.request_alternative(self.client_a, self.slot.pk, option.pk,
                                                          services.new_request_token())
        job.refresh_from_db()
        return job, fake

    def test_the_new_option_repeats_neither_the_meal_nor_other_meals_of_the_day(self):
        before = list(self.slot.options.filter(is_active=True).order_by('position'))
        others = set(MealOption.objects.filter(slot__nutrition_plan=self.plan.nutrition, is_active=True)
                     .exclude(slot=self.slot).values_list('name', flat=True))
        job, fake = self.alternative(before[1])
        self.assertEqual(job.status, job.STATUS_SUCCEEDED, job.error_code)
        replacement = self.slot.options.get(origin='client_regen')
        call = fake.calls[0]
        offered = set(names(call['payload']['recipe_options']))
        self.assertFalse(offered & others)                                          # otras comidas: fuera de la lista
        self.assertFalse(offered & {option.name for option in before})
        self.assertEqual(set(name_enum(call)), offered)
        self.assertNotIn(replacement.name, others | {option.name for option in before})
        self.assertTrue(others <= set(call['payload']['avoid_repeating_names']))
        # y prefiere una proteína que la comida todavía no tenía
        self.assertNotIn(recipes.protein_of(replacement.name), {recipes.protein_of(option.name) for option in before})
