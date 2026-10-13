"""Motor de reglas de la fase C (Python puro, sin base de datos): energía y macros, filtro de
alimentos, medidas caseras, intercambios, validación de la salida de la IA y datos del catálogo."""
import copy
from decimal import Decimal as D

from django.test import SimpleTestCase

from apps.plans.ai.fake import sample_meal_slot
from apps.plans.foods_data import DIETS_BY_CLASS, FOODS, SOURCE, food_rows
from apps.plans.rules import energy
from apps.plans.rules import foods as f
from apps.plans.rules import meal_validate as m


def inp(**overrides):
    base = dict(age=34, sex='F', weight_kg=D('68'), height_m=D('1.65'), activity_level='light', goal='weight_loss')
    base.update(overrides)
    return energy.EnergyInput(**base)


class EnergyTests(SimpleTestCase):
    def test_mifflin_st_jeor_known_values(self):
        plan = energy.compute_energy(inp())
        # TMB = 10*68 + 6.25*165 - 5*34 - 161 = 1380.25 ; GET = TMB * 1.375 = 1897.84
        self.assertEqual(plan.tmb, D('1380.3'))
        self.assertEqual(plan.get, D('1900'))
        self.assertEqual(plan.kcal, D('1520'))                       # déficit de 20 % (379 kcal < 500)
        self.assertEqual((plan.protein_g, plan.carbs_g, plan.fat_g), (D('109'), D('165'), D('47')))
        self.assertEqual(plan.adjustment_pct, D('-20.0'))
        self.assertEqual(plan.formula, 'mifflin_st_jeor/1')
        self.assertEqual(plan.warnings, ())

    def test_sex_constants(self):
        male = energy.compute_energy(inp(sex='M', weight_kg=D('80'), height_m=D('1.80'), age=30, goal='cardio',
                                         activity_level='sedentary'))
        self.assertEqual(male.tmb, D('1780.0'))                       # 800 + 1125 - 150 + 5
        na = energy.compute_energy(inp(sex='NA', goal='cardio'))
        self.assertEqual(na.tmb, D('1463.3'))                          # 680 + 1031.25 - 170 - 78

    def test_goal_adjustments(self):
        self.assertEqual(energy.compute_energy(inp(goal='cardio')).adjustment_kcal, 0)
        self.assertEqual(energy.compute_energy(inp(goal='toning')).adjustment_kcal, 0)       # IMC < 25
        heavy = dict(weight_kg=D('76'), height_m=D('1.65'))                                  # IMC 27,9
        toning = energy.compute_energy(inp(goal='toning', **heavy))
        self.assertLess(toning.adjustment_kcal, 0)
        self.assertLessEqual(-toning.adjustment_kcal, 300)
        gain = energy.compute_energy(inp(goal='muscle_gain'))
        self.assertGreater(gain.adjustment_kcal, 0)
        self.assertLessEqual(gain.adjustment_kcal, 300)

    def test_deficit_is_capped_at_500_kcal(self):
        plan = energy.compute_energy(inp(sex='M', weight_kg=D('130'), height_m=D('1.85'), age=25,
                                         activity_level='active'))
        self.assertGreaterEqual(plan.adjustment_kcal, D('-500'))      # 20 % serían más de 800 kcal
        self.assertLessEqual(plan.adjustment_kcal, D('-480'))

    def test_diabetes_caps_the_deficit_at_15_percent(self):
        normal = energy.compute_energy(inp())
        capped = energy.compute_energy(inp(diabetes_cap=True))
        self.assertEqual(capped.adjustment_pct, D('-15.0'))
        self.assertGreater(capped.kcal, normal.kcal)

    def test_calorie_floor_is_applied_when_it_fits_under_the_expenditure(self):
        plan = energy.compute_energy(inp(weight_kg=D('45'), height_m=D('1.50'), age=40, activity_level='sedentary'))
        self.assertEqual(plan.kcal, D('1200'))
        self.assertTrue(plan.floor_applied)
        self.assertIn(energy.W_FLOOR, plan.warnings)

    def test_no_deficit_when_the_floor_is_above_the_expenditure(self):
        plan = energy.compute_energy(inp(weight_kg=D('40'), height_m=D('1.45'), age=60, activity_level='sedentary'))
        self.assertLess(plan.get, 1200)
        self.assertEqual(plan.kcal, plan.get)                          # mantenimiento
        self.assertFalse(plan.floor_applied)
        self.assertIn(energy.W_NO_DEFICIT, plan.warnings)
        self.assertEqual(plan.adjustment_kcal, 0)

    def test_weekly_loss_stays_under_half_a_kilo(self):
        for weight in (50, 68, 90, 130):
            for activity in energy.ACTIVITY_FACTORS:
                for sex in ('F', 'M', 'NA'):
                    plan = energy.compute_energy(inp(weight_kg=D(weight), activity_level=activity, sex=sex))
                    self.assertGreater(plan.weekly_change_kg, D('-0.5'), (weight, activity, sex))

    def test_protein_uses_the_reference_weight_and_respects_caps(self):
        heavy = energy.compute_energy(inp(weight_kg=D('110'), height_m=D('1.60'), goal='weight_loss'))
        reference = D('27') * D('1.60') ** 2                           # 69,1 kg
        self.assertEqual(heavy.protein_g, (D('1.6') * reference).quantize(D('1')))
        self.assertLessEqual(heavy.protein_g * 4, heavy.kcal * D('0.35') + 2)
        gain = energy.compute_energy(inp(goal='muscle_gain'))
        self.assertEqual(gain.protein_g, (D('1.8') * D('68')).quantize(D('1')))
        other = energy.compute_energy(inp(goal='cardio'))
        self.assertEqual(other.protein_g, (D('1.2') * D('68')).quantize(D('1')))

    def test_macros_add_up_to_the_energy(self):
        for goal in ('weight_loss', 'toning', 'muscle_gain', 'cardio', 'sports', 'rehab'):
            plan = energy.compute_energy(inp(goal=goal))
            total = 4 * plan.protein_g + 4 * plan.carbs_g + 9 * plan.fat_g
            self.assertLessEqual(abs(total - plan.kcal), 25, goal)
            self.assertGreaterEqual(plan.fat_g * 9, plan.kcal * D('0.19'))
            self.assertGreaterEqual(plan.fiber_g, 25)

    def test_target_weight_below_bmi_20_is_flagged(self):
        plan = energy.compute_energy(inp(target_weight_kg=D('45')))
        self.assertIn(energy.W_TARGET_WEIGHT_LOW, plan.warnings)
        self.assertNotIn(energy.W_TARGET_WEIGHT_LOW, energy.compute_energy(inp(target_weight_kg=D('60'))).warnings)

    def test_impossible_inputs_raise(self):
        for bad in (dict(sex='X'), dict(activity_level='extreme'), dict(weight_kg=D('0')), dict(height_m=D('0'))):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                energy.compute_energy(inp(**bad))


class MealSplitTests(SimpleTestCase):
    def setUp(self):
        self.plan = energy.compute_energy(inp())

    def test_every_supported_split_adds_up(self):
        for slots in (['breakfast', 'lunch', 'dinner'], ['breakfast', 'lunch', 'snack', 'dinner'],
                      ['breakfast', 'mid_morning', 'lunch', 'dinner'],
                      ['breakfast', 'mid_morning', 'lunch', 'snack', 'dinner']):
            targets = energy.split_meals(self.plan, slots)
            with self.subTest(slots=slots):
                self.assertEqual(len(targets), len(slots))
                self.assertEqual(sum(t.percent for t in targets), 100)
                self.assertLessEqual(abs(sum(t.kcal for t in targets) - self.plan.kcal), 20)
                self.assertEqual([t.order for t in targets], list(range(len(slots))))

    def test_canonical_order_and_known_percentages(self):
        targets = energy.split_meals(self.plan, ['dinner', 'lunch', 'breakfast'])
        self.assertEqual([t.code for t in targets], ['breakfast', 'lunch', 'dinner'])
        self.assertEqual([t.percent for t in targets], [30, 40, 30])
        five = energy.split_meals(self.plan, ['snack', 'mid_morning', 'dinner', 'lunch', 'breakfast'])
        self.assertEqual([t.percent for t in five], [25, 10, 30, 10, 25])
        mid = energy.split_meals(self.plan, ['breakfast', 'mid_morning', 'lunch', 'dinner'])
        self.assertEqual([t.percent for t in mid], [25, 10, 35, 30])

    def test_unsupported_split_raises(self):
        for slots in ([], ['breakfast', 'lunch'], ['lunch', 'dinner', 'snack']):
            with self.subTest(slots=slots), self.assertRaises(ValueError):
                energy.split_meals(self.plan, slots)


# ─── Catálogo ─────────────────────────────────────────────────────────────────

def catalog_items():
    return [
        f.FoodItem(id=index + 1, name=row['name'], group=row['group'], kcal100=row['kcal100'],
                   protein100=row['protein100'], carbs100=row['carbs100'], fat100=row['fat100'],
                   fiber100=row['fiber100'], household_unit=row['household_unit'],
                   household_unit_plural=row['household_unit_plural'], household_grams=row['household_grams'],
                   allergen_tags=tuple(row['allergen_tags']), trace_tags=tuple(row['trace_tags']),
                   diet_tags=tuple(row['diet_tags']), condition_tags=tuple(row['condition_tags']),
                   is_common=row['is_common'])
        for index, row in enumerate(food_rows())
    ]


ITEMS = catalog_items()
BY_NAME = {item.name: item for item in ITEMS}


class StarterCatalogTests(SimpleTestCase):
    def test_data_is_consistent(self):
        rows = food_rows()
        self.assertGreaterEqual(len(rows), 80)
        self.assertEqual(len({row['name'] for row in rows}), len(rows))              # sin repetidos
        valid_groups = {code for code, _label in f.GROUPS}
        for row in rows:
            with self.subTest(food=row['name']):
                self.assertIn(row['group'], valid_groups)
                self.assertTrue(set(row['allergen_tags']) <= set(f.ALLERGEN_CODES))
                self.assertTrue(set(row['trace_tags']) <= set(f.ALLERGEN_CODES))
                self.assertTrue(set(row['condition_tags']) <= {code for code, _l in f.CONDITION_TAGS})
                self.assertTrue(row['diet_tags'] and set(row['diet_tags']) <= set(f.DIET_CODES))
                self.assertGreater(row['household_grams'], 0)
                self.assertTrue(row['household_unit'] and row['household_unit_plural'])
                self.assertEqual(row['source'], SOURCE)
                calc = 4 * row['protein100'] + 4 * row['carbs100'] + 9 * row['fat100']
                self.assertLessEqual(abs(calc - row['kcal100']), row['kcal100'] * D('0.15') + 10,
                                     'kcal no cuadra con los macros (¿error de digitación?)')

    def test_diet_classes_cover_the_known_diets(self):
        for diet_class, diets in DIETS_BY_CLASS.items():
            self.assertTrue(set(diets) <= set(f.DIET_CODES), diet_class)
        classes = {row[10] for row in FOODS}
        self.assertTrue(classes <= set(DIETS_BY_CLASS))

    def test_animal_foods_never_reach_vegan_or_vegetarian_diets(self):
        for item in ITEMS:
            if item.group == 'proteina_animal':
                self.assertNotIn('vegan', item.diet_tags, item.name)
            if item.name in ('Lomo de cerdo magro cocido',):
                self.assertEqual(item.diet_tags, ('omnivore',))
        self.assertNotIn('vegetarian', BY_NAME['Pechuga de pollo cocida sin piel'].diet_tags)
        self.assertIn('pescatarian', BY_NAME['Tilapia cocida'].diet_tags)
        self.assertNotIn('halal', BY_NAME['Camarón cocido'].diet_tags)

    def test_every_diet_leaves_enough_foods(self):
        for diet in f.DIET_CODES:
            catalog = f.filter_foods(ITEMS, f.NutritionProfile(diet=diet))
            self.assertTrue(catalog.enough, diet)


class FoodFilterTests(SimpleTestCase):
    def names(self, **profile):
        return {item.name for item in f.filter_foods(ITEMS, f.NutritionProfile(**profile)).allowed}

    def test_allergens_and_traces_are_excluded(self):
        peanut = self.names(allergies=('peanut',))
        self.assertNotIn('Maní tostado sin sal', peanut)
        self.assertNotIn('Almendras', peanut)                       # trazas de maní
        self.assertNotIn('Mantequilla de maní sin azúcar', peanut)
        self.assertIn('Nueces', self.names(allergies=('fish',)))
        milk = self.names(allergies=('milk',))
        self.assertFalse({'Leche entera', 'Queso fresco campesino', 'Mantequilla', 'Chocolate oscuro 70 %'} & milk)

    def test_celiac_and_lactose_map_to_allergens(self):
        celiac = self.names(conditions=('celiac',))
        self.assertFalse({'Pasta cocida', 'Pan integral tajado', 'Avena en hojuelas (cruda)'} & celiac)
        self.assertIn('Arroz blanco cocido', celiac)
        lactose = self.names(intolerances=('lactose',))
        self.assertNotIn('Leche entera', lactose)
        self.assertIn('Bebida de soya sin azúcar', lactose)

    def test_diets(self):
        vegan = self.names(diet='vegan')
        self.assertFalse({'Huevo entero cocido', 'Leche entera', 'Pechuga de pollo cocida sin piel', 'Miel de abeja'} & vegan)
        self.assertIn('Lentejas cocidas', vegan)
        self.assertNotIn('Lomo de cerdo magro cocido', self.names(diet='halal'))
        self.assertNotIn('Pechuga de pollo cocida sin piel', self.names(diet='vegetarian'))
        self.assertIn('Huevo entero cocido', self.names(diet='vegetarian'))
        everything = self.names(diet='other')
        self.assertEqual(len(everything), len(ITEMS))

    def test_yellow_flags_and_conditions_exclude_tagged_foods(self):
        hypertension = self.names(yellow_codes=('Y01_HYPERTENSION',))
        self.assertNotIn('Queso fresco campesino', hypertension)
        self.assertNotIn('Galletas de soda', hypertension)
        diabetes = self.names(yellow_codes=('Y02_DIABETES_T2',))
        self.assertNotIn('Miel de abeja', diabetes)
        self.assertNotIn('Miel de abeja', self.names(conditions=('prediabetes',)))
        gout = self.names(conditions=('gout',))
        self.assertNotIn('Camarón cocido', gout)
        self.assertNotIn('Manzana', self.names(conditions=('ibs',)))
        self.assertNotIn('Naranja', self.names(conditions=('reflux_gastritis',)))
        self.assertNotIn('Mango', self.names(intolerances=('fructose',)))

    def test_not_enough_foods(self):
        few = f.filter_foods(ITEMS[:20], f.NutritionProfile())
        self.assertFalse(few.enough)
        no_protein = [i for i in ITEMS if i.protein100 < 10]
        self.assertFalse(f.filter_foods(no_protein, f.NutritionProfile()).enough)


class MeasuresTests(SimpleTestCase):
    def test_household_text(self):
        rice = BY_NAME['Arroz blanco cocido']                       # taza cocida = 160 g
        self.assertEqual(f.household_text(rice, 160), '1 taza cocida (160 g)')
        self.assertEqual(f.household_text(rice, 240), '1 ½ tazas cocidas (240 g)')
        self.assertEqual(f.household_text(rice, 40), '¼ taza cocida (40 g)')
        self.assertEqual(f.household_text(rice, 80), '½ taza cocida (80 g)')
        self.assertEqual(f.household_text(rice, 320), '2 tazas cocidas (320 g)')
        bare = f.FoodItem(id=1, name='x', group='cereales', kcal100=D(1), protein100=D(0), carbs100=D(0), fat100=D(0))
        self.assertEqual(f.household_text(bare, 55), '55 g')

    def test_totals_and_rounding(self):
        rice, chicken = BY_NAME['Arroz blanco cocido'], BY_NAME['Pechuga de pollo cocida sin piel']
        total = f.items_totals([(rice, 100), (chicken, 100)])
        self.assertEqual(total.kcal, D('295'))
        self.assertEqual(total.protein, D('33.7'))
        self.assertEqual(f.round_totals(total)['kcal'], 295)
        self.assertEqual(round(float(total.fat_share), 3), round(float(D('3.9') * 9 / D('295')), 3))

    def test_exchanges_give_the_same_energy_per_portion(self):
        groups = f.exchange_groups(ITEMS)
        self.assertGreaterEqual(len(groups), 6)
        for group in groups:
            for row in group['rows']:
                energy_kcal = row['food'].kcal100 * row['grams'] / 100
                tolerance = max(D('0.12') * group['portion_kcal'], row['food'].kcal100 / 100 * 3)
                self.assertLessEqual(abs(energy_kcal - group['portion_kcal']), tolerance, row['food'].name)
                self.assertLessEqual(row['grams'], 600)
                self.assertTrue(row['household'].endswith('g)'))


# ─── Validación de la salida de la IA ─────────────────────────────────────────

LUNCH_KCAL, LUNCH_PROTEIN = D('530'), D('38')


def slot_payload(catalog, kcal=LUNCH_KCAL, protein=LUNCH_PROTEIN, n=3, existing=()):
    return {
        'job_ref': 'prueba', 'slot': {'code': 'lunch', 'target_kcal': float(kcal), 'protein_g': float(protein)},
        'options_requested': n, 'avoid_repeating_names': [], 'existing_options': list(existing),
        'allowed_foods': [{'id': i.id, 'name': i.name, 'group': i.group, 'kcal100': float(i.kcal100),
                           'p100': float(i.protein100), 'c100': float(i.carbs100), 'f100': float(i.fat100),
                           'common': i.is_common} for i in catalog.allowed],
    }


class MealValidationTests(SimpleTestCase):
    def setUp(self):
        self.catalog = f.filter_foods(ITEMS, f.NutritionProfile())
        self.rules = m.SlotRules(slot_code='lunch', kcal=LUNCH_KCAL, protein_g=LUNCH_PROTEIN,
                                 foods=self.catalog.by_id)
        self.good = sample_meal_slot(slot_payload(self.catalog))

    def check(self, data, rules=None):
        return m.validate_meal_slot(data, rules or self.rules)

    def mutated(self):
        return copy.deepcopy(self.good)

    def test_baseline_is_valid_and_portions_are_adjusted(self):
        self.assertEqual(len(self.good['options']), 3)
        result = self.check(self.good)
        self.assertTrue(result.ok, result.errors)
        for option in result.data['options']:
            self.assertTrue(m.kcal_in_band(option['totals'].kcal, LUNCH_KCAL))
            self.assertGreaterEqual(option['totals'].protein, LUNCH_PROTEIN * D('0.9'))
            self.assertGreaterEqual(option['totals'].fat_share, D('0.2'))
            for item in option['items']:
                self.assertEqual(item['grams'] % 5, 0)
                self.assertTrue(5 <= item['grams'] <= 600)

    def test_schema_errors(self):
        data = self.mutated()
        data['extra'] = 1
        self.assertEqual(self.check(data).codes, ('V01_SCHEMA',))
        for key, value in (('schema_version', 'meal-slot/2'), ('slot_code', 'dinner')):
            data = self.mutated()
            data[key] = value
            self.assertIn('V01_SCHEMA', self.check(data).codes, key)
        self.assertEqual(self.check(['no']).codes, ('V01_SCHEMA',))
        data = self.mutated()
        data['options'] = data['options'][:2]
        self.assertIn('V01_SCHEMA', self.check(data).codes)                    # exactamente 3 opciones
        data = self.mutated()
        data['options'].append(copy.deepcopy(data['options'][0]))
        self.assertIn('V01_SCHEMA', self.check(data).codes)
        data = self.mutated()
        data['options'][0]['items'] = data['options'][0]['items'][:1]
        self.assertIn('V01_SCHEMA', self.check(data).codes)                    # 2 a 6 alimentos
        for field, value in (('grams', 4), ('grams', 601), ('grams', True), ('grams', '100'), ('food_id', '3')):
            data = self.mutated()
            data['options'][0]['items'][0][field] = value
            self.assertIn('V01_SCHEMA', self.check(data).codes, (field, value))
        data = self.mutated()
        data['options'][0]['items'][1]['food_id'] = data['options'][0]['items'][0]['food_id']
        self.assertIn('V01_SCHEMA', self.check(data).codes)                    # alimento repetido

    def test_unknown_or_filtered_food_is_rejected(self):
        data = self.mutated()
        data['options'][0]['items'][0]['food_id'] = 987654321
        self.assertEqual(self.check(data).codes, ('V02_IDS',))
        peanut = BY_NAME['Maní tostado sin sal']
        restricted = m.SlotRules(slot_code='lunch', kcal=LUNCH_KCAL, protein_g=LUNCH_PROTEIN,
                                 foods={k: v for k, v in self.catalog.by_id.items() if k != peanut.id})
        data = self.mutated()
        data['options'][0]['items'][0]['food_id'] = peanut.id
        self.assertEqual(self.check(data, restricted).codes, ('V02_IDS',))

    def test_text_rules(self):
        for field, text in (('name', '<b>Pollo</b>'), ('preparation', 'Mira https://ejemplo.com'),
                            ('preparation', 'Escribe a coach@correo.com'), ('preparation', 'Llama al 300 555 1234'),
                            ('preparation', 'Tómalo con un suplemento'), ('preparation', 'Haz ayuno de 16 horas'),
                            ('name', ''), ('name', 'x' * 101), ('preparation', 'x' * 401)):
            data = self.mutated()
            data['options'][0][field] = text
            self.assertIn('V08_TEXT', self.check(data).codes, (field, text))

    def test_allergen_words_in_free_text(self):
        def blocked(allergen, text, diet=''):
            data = self.mutated()
            data['options'][0]['name'] = text
            # Como en producción: el catálogo ya viene filtrado por la misma restricción
            allowed = f.filter_foods(ITEMS, f.NutritionProfile(allergies=(allergen,) if allergen else (),
                                                               diet=diet or 'omnivore'))
            rules = m.SlotRules(slot_code='lunch', kcal=LUNCH_KCAL, protein_g=LUNCH_PROTEIN,
                                foods=allowed.by_id, blocked_allergens=(allergen,) if allergen else (), diet=diet)
            return 'V03_ALLERGEN' in self.check(data, rules).codes

        for allergen, text in (('peanut', 'Pollo con salsa de Maní'), ('peanut', 'POLLO CON MANI'),
                               ('peanut', 'Pollo con cacahuate'), ('tree_nut', 'Ensalada con nueces'),
                               ('milk', 'Arroz con Queso'), ('milk', 'Crema de ahuyama'), ('milk', 'Con yogurt'),
                               ('egg', 'Huevos pericos'), ('egg', 'Omelette de espinaca'),
                               ('gluten', 'Pasta con tomate'), ('gluten', 'Pan con aguacate'),
                               ('gluten', 'Torta de harina'), ('fish', 'Sudado de pescado'),
                               ('fish', 'Atún con arroz'), ('shellfish', 'Arroz con camarones'),
                               ('shellfish', 'Cóctel de langostinos'), ('soy', 'Salteado con tofu'),
                               ('sesame', 'Pollo con ajonjolí')):
            self.assertTrue(blocked(allergen, text), (allergen, text))
        for diet, text in (('vegan', 'Pollo con arroz'), ('vegan', 'Arroz con huevo'), ('vegetarian', 'Sopa de res'),
                           ('pescatarian', 'Carne asada'), ('halal', 'Arroz con tocino'), ('kosher', 'Camarones al ajillo')):
            self.assertTrue(blocked('', text, diet), (diet, text))
        # Sin falsos positivos por palabras parecidas o por alérgenos que no tiene
        self.assertFalse(blocked('gluten', 'Arepa con panela'))
        self.assertFalse(blocked('peanut', 'Pollo con arroz y ensalada'))
        self.assertFalse(blocked('milk', 'Lentejas con arroz'))
        self.assertFalse(blocked('', 'Pollo con arroz', 'omnivore'))
        # Un alimento permitido cuyo nombre contiene una palabra de la lista no es una falla
        self.assertFalse(blocked('milk', 'Arroz con mantequilla de maní'))
        self.assertTrue(blocked('milk', 'Arroz con mantequilla'))

    def test_energy_band(self):
        richest = max(self.catalog.allowed, key=lambda item: item.kcal100)
        other = next(item for item in self.catalog.allowed if item.id != richest.id)
        data = self.mutated()
        data['options'][0]['items'] = [{'food_id': richest.id, 'grams': 600}, {'food_id': other.id, 'grams': 5}]
        self.assertIn('V04_KCAL', self.check(data).codes)
        tiny = self.mutated()
        tiny['options'][0]['items'] = [{'food_id': BY_NAME['Lechuga'].id, 'grams': 50},
                                       {'food_id': BY_NAME['Pepino cohombro'].id, 'grams': 50}]
        self.assertIn('V04_KCAL', self.check(tiny).codes)

    def test_macros_minimums(self):
        # Solo fruta: la energía se alcanza al escalar, pero faltan proteína y grasa
        fruit = self.mutated()
        fruit['options'][0]['items'] = [{'food_id': BY_NAME['Banano'].id, 'grams': 300},
                                        {'food_id': BY_NAME['Uvas'].id, 'grams': 300}]
        self.assertIn('V05_MACROS', self.check(fruit).codes)

    def test_variety(self):
        same_name = self.mutated()
        same_name['options'][1]['name'] = same_name['options'][0]['name'].upper()
        self.assertIn('V06_VARIETY', self.check(same_name).codes)
        same_foods = self.mutated()
        same_foods['options'][1]['items'] = copy.deepcopy(same_foods['options'][0]['items'])
        same_foods['options'][1]['name'] = 'Otro nombre distinto'
        self.assertIn('V06_VARIETY', self.check(same_foods).codes)

    def test_new_option_must_differ_from_existing_ones(self):
        alone = sample_meal_slot(slot_payload(self.catalog, n=1))
        rules = m.SlotRules(slot_code='lunch', kcal=LUNCH_KCAL, protein_g=LUNCH_PROTEIN, foods=self.catalog.by_id,
                            n_options=1)
        self.assertTrue(self.check(alone, rules).ok)
        option = alone['options'][0]
        clone = m.ExistingOption(name='Otro', food_ids=frozenset(i['food_id'] for i in option['items']))
        self.assertIn('V06_VARIETY', self.check(alone, m.SlotRules(
            slot_code='lunch', kcal=LUNCH_KCAL, protein_g=LUNCH_PROTEIN, foods=self.catalog.by_id, n_options=1,
            existing=(clone,))).codes)
        same_name = m.ExistingOption(name=option['name'], food_ids=frozenset({-1}))
        self.assertIn('V06_VARIETY', self.check(alone, m.SlotRules(
            slot_code='lunch', kcal=LUNCH_KCAL, protein_g=LUNCH_PROTEIN, foods=self.catalog.by_id, n_options=1,
            existing=(same_name,))).codes)

    def test_scaling_moves_portions_toward_the_target(self):
        chicken, rice, tomato = (BY_NAME['Pechuga de pollo cocida sin piel'], BY_NAME['Arroz blanco cocido'],
                                 BY_NAME['Tomate'])
        rows = [(chicken, 80), (rice, 100), (tomato, 80)]
        raw = f.items_totals(rows).kcal
        scaled = m.scale_option(rows, LUNCH_KCAL)
        after = f.items_totals(scaled).kcal
        self.assertLess(abs(after - LUNCH_KCAL), abs(raw - LUNCH_KCAL))
        self.assertTrue(all(g % 5 == 0 for _food, g in scaled))
        # Nunca más allá de 0,7-1,3
        huge = m.scale_option([(chicken, 600), (rice, 600)], D('100'))
        self.assertTrue(all(g >= 420 for _food, g in huge))

    def test_trainer_option_validation(self):
        items = [{'food_id': BY_NAME['Pechuga de pollo cocida sin piel'].id, 'grams': 150},
                 {'food_id': BY_NAME['Arroz blanco cocido'].id, 'grams': 150},
                 {'food_id': BY_NAME['Tomate'].id, 'grams': 100}]
        result = m.validate_trainer_option('Pollo con arroz', 'Cocina y sirve.', items, self.rules)
        self.assertTrue(result.ok, result.errors)
        light = m.validate_trainer_option('Solo ensalada', 'Mezcla.', [
            {'food_id': BY_NAME['Lechuga'].id, 'grams': 50}, {'food_id': BY_NAME['Tomate'].id, 'grams': 50}], self.rules)
        self.assertTrue(light.ok)
        self.assertTrue(light.out_of_band)
        self.assertTrue(light.low_protein)
        bad = m.validate_trainer_option('Pollo', 'Cocina.', [{'food_id': 999999, 'grams': 100}, items[0]], self.rules)
        self.assertEqual({code for code, _ in bad.errors}, {'V02_IDS'})
        allergy = m.SlotRules(slot_code='lunch', kcal=LUNCH_KCAL, protein_g=LUNCH_PROTEIN, foods=self.catalog.by_id,
                              blocked_allergens=('milk',))
        self.assertIn('V03_ALLERGEN', {c for c, _ in m.validate_trainer_option(
            'Arroz con queso', 'Cocina.', items, allergy).errors})
        self.assertFalse(m.validate_trainer_option('', 'x', items, self.rules).ok)


class DayBandTests(SimpleTestCase):
    def totals(self, kcal, protein=30, fat_share=D('0.3')):
        return f.Totals(D(kcal), D(protein), D(kcal) * (1 - fat_share) / 4, D(kcal) * fat_share / 9)

    def test_all_combinations_inside_the_band(self):
        slots = [[self.totals(k) for k in (380, 390, 400)], [self.totals(k) for k in (520, 530, 540)],
                 [self.totals(k) for k in (370, 380, 390)]]
        self.assertEqual(m.check_day(slots, 1300, 90, 1200), [])

    def test_a_combination_outside_the_band_is_caught(self):
        slots = [[self.totals(380), self.totals(480)], [self.totals(530)], [self.totals(380)]]
        self.assertEqual(m.check_day(slots, 1300, 90, 1200), ['V04_KCAL'])

    def test_protein_and_fat_minimums(self):
        weak = [[self.totals(430, protein=20)] for _ in range(3)]
        self.assertEqual(m.check_day(weak, 1290, 90, 1200), ['V05_MACROS'])
        lean = [[self.totals(430, fat_share=D('0.1'))] for _ in range(3)]
        self.assertIn('V05_MACROS', m.check_day(lean, 1290, 90, 1200))

    def test_floor_above_target_uses_the_target(self):
        slots = [[self.totals(335)], [self.totals(335)], [self.totals(335)]]
        self.assertEqual(m.check_day(slots, 1005, 90, 1200), [])
