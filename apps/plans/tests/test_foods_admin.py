"""Catálogo de alimentos: comando `load_foods` y administración (solo la superusuaria)."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.accounts.tests.helpers import ScenarioTestCase
from apps.plans.foods_data import food_rows
from apps.plans.models import Food


class LoadFoodsTests(TestCase):
    def load(self):
        out = StringIO()
        call_command('load_foods', stdout=out)
        return out.getvalue()

    def test_loads_once_and_is_idempotent(self):
        out = self.load()
        self.assertEqual(Food.objects.count(), len(food_rows()))
        self.assertIn('nuevos', out)
        self.assertIn('SIN VALIDAR', out)
        self.assertFalse(Food.objects.exclude(validated_by='').exists())
        self.load()
        self.assertEqual(Food.objects.count(), len(food_rows()))

    def test_never_overwrites_a_validated_food(self):
        self.load()
        rice = Food.objects.get(name='Arroz blanco cocido')
        rice.kcal100, rice.validated_by = 128, 'Nutricionista, 01/01/2027'
        rice.save()
        out = self.load()
        rice.refresh_from_db()
        self.assertEqual(rice.kcal100, 128)
        self.assertIn('ya validados', out)
        other = Food.objects.get(name='Lechuga')
        other.kcal100 = 99
        other.save()
        self.load()
        other.refresh_from_db()
        self.assertEqual(other.kcal100, 15)                      # el no validado vuelve al valor inicial


class FoodAdminTests(ScenarioTestCase):
    def setUp(self):
        super().setUp()
        call_command('load_foods', stdout=StringIO())

    def test_only_the_superuser_reaches_the_catalog(self):
        url = reverse('admin:plans_food_changelist')
        self.login(self.boss)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.login(self.trainer_a)
        self.assertNotEqual(self.client.get(url).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_validation_actions(self):
        self.login(self.boss)
        url = reverse('admin:plans_food_changelist')
        ids = list(Food.objects.values_list('pk', flat=True)[:3])
        response = self.client.post(url, {'action': 'mark_validated', '_selected_action': ids}, follow=True)
        self.assertEqual(response.status_code, 200)
        validated = Food.objects.filter(pk__in=ids)
        self.assertTrue(all(food.validated_by.startswith('Boss Prueba') for food in validated))
        self.client.post(url, {'action': 'clear_validation', '_selected_action': ids})
        self.assertFalse(Food.objects.exclude(validated_by='').exists())
