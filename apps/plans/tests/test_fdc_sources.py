"""USDA sin red: formato, unidades, secretos, importación e historial."""
import copy
import io
import json
import urllib.error
from decimal import Decimal
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from apps.plans.models import Food, FoodImportRevision
from apps.plans.sources.fdc import FDCClient, FDCError
from apps.plans.sources.fdc_import import import_food
from apps.plans.sources.fdc_normalize import normalize_food
from apps.plans.rules import foods
from apps.plans import nutrient_tables
from .helpers import NutritionScenarioTestCase

# Subconjunto de la respuesta pública FDC 171077, consultada 2026-11-17.
# Es pollo CRUDO: no debe vincularse a la pechuga cocida del catálogo.
DESCRIPTION = 'Chicken, broiler or fryers, breast, skinless, boneless, meat only, raw'


def sample():
    return {
        'fdcId': 171077, 'description': DESCRIPTION, 'dataType': 'SR Legacy',
        'publicationDate': '4/1/2019',
        'foodNutrients': [
            {'nutrient': {'id': identifier, 'unitName': unit}, 'amount': value}
            for identifier, unit, value in (
                (1008, 'kcal', 120), (1003, 'g', 22.5), (1004, 'g', 2.62),
                (1005, 'g', 0), (1079, 'g', 0), (2000, 'g', 0),
                (1093, 'mg', 45), (1258, 'g', 0.563),
            )
        ],
    }


class FDCNormalizeTests(SimpleTestCase):
    def test_real_format_uses_nutrient_id_and_preserves_known_zero(self):
        values, snapshot, digest = normalize_food(sample(), 171077)
        self.assertEqual(values['sodium_mg100'], Decimal('45.000'))
        self.assertEqual(values['fat100'], Decimal('2.620'))
        self.assertEqual(values['sugars100'], Decimal('0.000'))
        self.assertIsNone(values['added_sugars100'])
        self.assertEqual(snapshot['basis'], '100g')
        self.assertEqual(len(digest), 64)

    def test_missing_is_not_zero_and_label_nutrients_are_not_per100g(self):
        payload = sample()
        payload['foodNutrients'] = payload['foodNutrients'][:4]
        payload['labelNutrients'] = {'sodium': {'value': 500}, 'sugars': {'value': 12}}
        values, _, _ = normalize_food(payload, 171077)
        self.assertIsNone(values['sodium_mg100'])
        self.assertIsNone(values['sugars100'])
        self.assertIsNone(values['fiber100'])

    def test_rejects_units_bad_amount_and_missing_macros(self):
        for value in (-1, 'NaN', 'Infinity', True, 100001):
            payload = sample()
            payload['foodNutrients'][6]['amount'] = value
            with self.subTest(value=value), self.assertRaises(FDCError):
                normalize_food(payload, 171077)
        payload = sample()
        payload['foodNutrients'][6]['nutrient']['unitName'] = 'g'
        with self.assertRaises(FDCError):
            normalize_food(payload, 171077)
        with self.assertRaises(FDCError):
            normalize_food({**sample(), 'foodNutrients': []}, 171077)

    def test_id_type_energy_fallback_and_consistency(self):
        with self.assertRaises(FDCError):
            normalize_food(sample(), 999)
        payload = sample()
        payload['foodNutrients'][0]['nutrient']['id'] = 2047
        self.assertEqual(normalize_food(payload, 171077)[0]['kcal100'], Decimal('120'))
        payload['foodNutrients'].append({'nutrient': {'id': 1235, 'unitName': 'g'}, 'amount': 3})
        with self.assertRaises(FDCError):
            normalize_food(payload, 171077)

    def test_branded_volume_is_not_assumed_to_be_grams(self):
        for unit in ('ml', 'fl oz', ''):
            with self.subTest(unit=unit), self.assertRaises(FDCError):
                normalize_food({**sample(), 'dataType': 'Branded', 'servingSizeUnit': unit}, 171077)
        values, _, _ = normalize_food({**sample(), 'dataType': 'Branded', 'servingSizeUnit': 'g'}, 171077)
        self.assertEqual(values['sodium_mg100'], Decimal('45'))


class FDCClientTests(SimpleTestCase):
    def test_key_is_header_only_and_repr_does_not_expose_it(self):
        body = io.BytesIO(json.dumps(sample()).encode())
        opener = mock.Mock(return_value=body)
        client = FDCClient('synthetic-secret-key', opener=opener)
        self.assertEqual(client.food(171077)['fdcId'], 171077)
        request = opener.call_args.args[0]
        self.assertNotIn('synthetic-secret-key', request.full_url)
        self.assertEqual(dict((k.lower(), v) for k, v in request.header_items())['x-api-key'], 'synthetic-secret-key')
        self.assertNotIn('synthetic-secret-key', repr(client))
        self.assertNotIn('api_key=', request.full_url)

    def test_errors_do_not_repeat_secret_url_or_body(self):
        for cause in (
            urllib.error.URLError('https://host/?api_key=synthetic-secret'),
            urllib.error.HTTPError('https://host/?api_key=synthetic-secret', 429, 'secret message', {}, io.BytesIO(b'secret')),
        ):
            client = FDCClient('synthetic-secret', opener=mock.Mock(side_effect=cause))
            with self.assertRaises(FDCError) as ctx:
                client.food(171077)
            self.assertNotIn('secret', str(ctx.exception))
            self.assertIsNone(ctx.exception.__cause__)

    def test_bad_json_and_response_size(self):
        from apps.plans.sources.fdc import MAX_RESPONSE_BYTES
        for body in (b'bad JSON', b'[]', b'x' * (MAX_RESPONSE_BYTES + 1)):
            client = FDCClient('fake', opener=mock.Mock(return_value=io.BytesIO(body)))
            with self.assertRaises(FDCError):
                client.food(171077)


class FDCImportTests(TestCase):
    def apply(self, **overrides):
        args = dict(name='Pechuga cruda USDA', group='proteina_animal', state='raw',
                    confirm_description=DESCRIPTION, apply=True)
        args.update(overrides)
        return import_food(sample(), **args)

    def test_preview_is_read_only_and_apply_creates_inactive_with_revision(self):
        food, snapshot, changed = self.apply(apply=False)
        self.assertIsNone(food)
        self.assertFalse(changed)
        self.assertEqual(Food.objects.count(), 0)
        food, _, changed = self.apply()
        self.assertTrue(changed)
        self.assertFalse(food.is_active)
        self.assertEqual(food.validated_by, '')
        self.assertEqual(food.fdc_id, 171077)
        self.assertEqual(food.sodium_mg100, Decimal('45'))
        self.assertEqual(food.import_revisions.count(), 1)

    def test_confirmation_idempotency_and_reviewed_data_are_protected(self):
        with self.assertRaises(FDCError):
            self.apply(confirm_description='another food')
        food, _, _ = self.apply()
        food.is_active = True
        food.validated_by = 'Revisor de prueba'
        food.save()
        same, _, changed = self.apply(food_id=food.pk, name=None)
        self.assertFalse(changed)
        self.assertTrue(same.is_active)
        food.kcal100 = 121
        food.source_hash = 'manually_changed'
        food.save()
        with self.assertRaises(FDCError):
            self.apply(food_id=food.pk, name=None)
        self.assertEqual(FoodImportRevision.objects.count(), 1)

    def test_seed_loader_does_not_overwrite_imported_unvalidated_food(self):
        food, _, _ = self.apply(name='Pechuga de pollo cocida sin piel')
        # Deliberadamente mal llamado para probar la protección del cargador, no identidad culinaria.
        call_command('load_foods', stdout=io.StringIO())
        food.refresh_from_db()
        self.assertEqual(food.kcal100, Decimal('120'))
        self.assertEqual(food.fdc_id, 171077)
        self.assertFalse(food.is_active)

    @override_settings(FDC_API_KEY='fake-key', FDC_TIMEOUT_SECONDS=15)
    def test_command_preview_and_apply(self):
        args = ['--fdc-id', '171077', '--name', 'Pollo crudo de prueba', '--group', 'proteina_animal', '--state', 'raw']
        with mock.patch('apps.plans.sources.fdc.FDCClient.food', return_value=sample()):
            out = io.StringIO()
            call_command('fdc_import', *args, stdout=out)
            self.assertEqual(Food.objects.count(), 0)
            self.assertIn('preview_only', out.getvalue())
            call_command('fdc_import', *args, '--apply', '--confirm-description', DESCRIPTION, stdout=io.StringIO())
        self.assertEqual(Food.objects.count(), 1)


class NutrientMathTests(SimpleTestCase):
    def test_portion_units_and_incomplete_total(self):
        a = foods.FoodItem(1, 'Example', 'cereales', Decimal('100'), Decimal('5'), Decimal('10'), Decimal('3'),
                           sodium_mg100=Decimal('45'), sugars100=Decimal('2'))
        total = foods.items_totals([(a, 150)])
        self.assertEqual(total.sodium_mg, Decimal('67.5'))
        self.assertEqual(total.sugars, Decimal('3'))
        self.assertIsNone(total.added_sugars)
        missing = foods.FoodItem(2, 'Unknown', 'cereales', Decimal('100'), Decimal('5'), Decimal('10'), Decimal('3'))
        combined = total + foods.portion_totals(missing, 50)
        self.assertIsNone(combined.sodium_mg)
        self.assertIsNone(foods.round_totals(combined)['sodium_mg'])

    def test_limit_comparison_never_claims_unknown_complies(self):
        total = foods.Totals(sodium_mg=None)
        self.assertEqual(nutrient_tables.compare_limits(total, {'sodium_mg': 2000})['sodium_mg']['status'], 'unknown')
        self.assertEqual(nutrient_tables.compare_limits(foods.Totals(sodium_mg=Decimal('2100')),
                                                       {'sodium_mg': 2000})['sodium_mg']['status'], 'above')


class NutritionSnapshotsTests(NutritionScenarioTestCase):
    def test_saved_plan_keeps_source_values_after_food_updates(self):
        Food.objects.update(sodium_mg100=Decimal('100'), sugars100=Decimal('1'), added_sugars100=Decimal('0'),
                            saturated_fat100=Decimal('0.1'))
        plan = self.nutrition_draft(self.client_a)
        option = plan.nutrition.slots.first().options.first()
        original = nutrient_tables.option_table(option)
        self.assertGreater(original['sodium_mg'], 0)
        self.assertEqual(original['added_sugars'], 0)
        Food.objects.update(sodium_mg100=Decimal('0'), sugars100=Decimal('90'))
        option.refresh_from_db()
        self.assertEqual(nutrient_tables.option_table(option), original)
        self.assertTrue(option.nutrient_snapshot['ingredients'])
        option.nutrient_snapshot = {}
        self.assertIsNone(nutrient_tables.option_table(option)['sodium_mg'])

