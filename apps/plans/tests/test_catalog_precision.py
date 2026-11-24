"""Composición, identidad, política de fuentes y flujo de recetas; sin red."""

import copy
import io
import json
from decimal import Decimal
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from apps.plans import services, nutrient_tables
from apps.plans.models import Food, FoodImportRevision, KIND_NUTRITION
from apps.plans.sources.catalog import (
    load_reference,
    source_is_consistent,
    reference_report,
)
from apps.plans.sources.fdc import FDCError
from apps.plans.sources.fdc_import import import_food
from apps.plans.sources.fdc_normalize import normalize_food
from .test_fdc_sources import sample, DESCRIPTION
from .helpers import PlanScenarioTestCase


class SourcePrecisionTests(SimpleTestCase):
    def test_catalog_covers_all_90_without_assigning_dubious_equivalences(self):
        data, reviewed, pending = load_reference()
        self.assertEqual((len(reviewed), len(pending)), (70, 20))
        self.assertEqual(len({entry["name"] for entry in data["foods"]}), 90)
        unresolved = {entry["name"] for entry in pending}
        self.assertIn("Yuca cocida", unresolved)
        self.assertIn("Mantequilla de maní sin azúcar", unresolved)
        self.assertIn("Papa criolla cocida", unresolved)
        for entry in reviewed:
            self.assertEqual(
                normalize_food(entry["record"], entry["fdc_id"])[2],
                entry["source_hash"],
            )
            if not any(
                row["nutrient"]["id"] == 1235 and row.get("amount") is not None
                for row in entry["record"]["foodNutrients"]
            ):
                self.assertIsNone(entry["values"]["added_sugars100"])

    def test_reference_identity_is_not_just_a_matching_calorie_value(self):
        _, entries, _ = load_reference()
        indexed = {entry["name"]: entry for entry in entries}
        identities = {
            "Atún en agua (escurrido)": ("tuna", "canned in water", "drained solids"),
            "Avena en hojuelas (cruda)": ("oats", "dry"),
            "Manzana": ("apples", "raw", "with skin"),
            "Tortilla de maíz": ("tortillas", "corn"),
            "Pechuga de pollo cocida sin piel": (
                "chicken",
                "breast",
                "meat only",
                "cooked",
            ),
            "Yogur griego natural": ("yogurt", "greek", "whole milk"),
        }
        for name, required in identities.items():
            with self.subTest(name=name):
                description = indexed[name]["expected_description"].casefold()
                self.assertTrue(
                    all(term in description for term in required), description
                )
        self.assertNotIn(
            "fresh",
            indexed["Atún en agua (escurrido)"]["expected_description"].casefold(),
        )

    def test_modified_reference_is_rejected_before_import(self):
        data, _, _ = load_reference()
        data = copy.deepcopy(data)
        entry = next(
            item for item in data["foods"] if item["status"] == "reviewed_reference"
        )
        entry["record"]["foodNutrients"][0]["amount"] += 1
        with mock.patch("apps.plans.sources.catalog.DATA_PATH") as path:
            path.read_text.return_value = json.dumps(data)
            with self.assertRaisesMessage(FDCError, "fdc_catalog_hash_mismatch"):
                load_reference()

    def test_audit_and_preview_do_not_query_a_database(self):
        self.assertEqual(reference_report()["total"], 90)
        out = io.StringIO()
        call_command("load_usda_catalog", stdout=out)
        self.assertIn("preview_only", out.getvalue())
        out = io.StringIO()
        call_command("audit_food_catalog", stdout=out)
        self.assertIn("needs_specification", out.getvalue())

    def test_duplicate_energy_and_fallback_sugar_cannot_silently_overwrite(self):
        payload = sample()
        payload["foodNutrients"][0]["nutrient"]["id"] = 2048
        duplicate = copy.deepcopy(payload["foodNutrients"][0])
        duplicate["amount"] = 200
        payload["foodNutrients"].append(duplicate)
        with self.assertRaisesMessage(FDCError, "fdc_conflicting_nutrients"):
            normalize_food(payload, 171077)
        payload = sample()
        payload["foodNutrients"] += [
            {"nutrient": {"id": 1063, "unitName": "g"}, "amount": 1},
            {"nutrient": {"id": 1063, "unitName": "g"}, "amount": 2},
        ]
        with self.assertRaisesMessage(FDCError, "fdc_conflicting_nutrients"):
            normalize_food(payload, 171077)

    def test_bad_identifier_and_metadata_have_controlled_errors(self):
        for identifier in ({"id": 1008}, [1008], "1008", True):
            payload = sample()
            payload["foodNutrients"][0]["nutrient"]["id"] = identifier
            with self.subTest(identifier=identifier), self.assertRaises(FDCError):
                normalize_food(payload, 171077)
        payload = sample()
        payload["foodNutrients"][0]["foodNutrientDerivation"] = "unexpected"
        with self.assertRaises(FDCError):
            normalize_food(payload, 171077)

    def test_source_amount_preserves_precision_before_storage_rounding(self):
        payload = sample()
        payload["foodNutrients"][1]["amount"] = 22.55555
        values, snapshot, _ = normalize_food(payload, 171077)
        self.assertEqual(values["protein100"], Decimal("22.556"))
        self.assertEqual(
            snapshot["evidence"]["protein100"]["source_amount"], "22.55555"
        )

    def test_activation_requires_explicit_apply(self):
        with self.assertRaisesMessage(CommandError, "fdc_catalog_activation_requires_apply"):
            call_command("load_usda_catalog", activate=True)


class CatalogImportTests(TestCase):
    def test_source_raw_cannot_be_imported_as_cooked_or_named_cooked(self):
        args = dict(
            name="Pechuga cruda",
            group="proteina_animal",
            state="cooked",
            confirm_description=DESCRIPTION,
            apply=True,
        )
        with self.assertRaisesMessage(FDCError, "fdc_source_preparation_mismatch"):
            import_food(sample(), **args)
        args.update(state="raw", name="Pechuga cocida")
        with self.assertRaisesMessage(FDCError, "fdc_local_preparation_mismatch"):
            import_food(sample(), **args)
        self.assertFalse(Food.objects.exists())

    def test_import_does_not_retain_an_unverified_household_measure(self):
        food = Food.objects.create(
            name="Pechuga cruda",
            group="proteina_animal",
            kcal100=100,
            protein100=10,
            carbs100=0,
            fat100=2,
            household_unit="filete",
            household_unit_plural="filetes",
            household_grams=123,
        )
        food, _, _ = import_food(
            sample(),
            food_id=food.pk,
            state="raw",
            confirm_description=DESCRIPTION,
            apply=True,
        )
        self.assertEqual(food.household_unit, "")
        self.assertIsNone(food.household_grams)
        self.assertTrue(source_is_consistent(food))
        food.sodium_mg100 = Decimal("999")
        self.assertFalse(source_is_consistent(food))

    def test_offline_load_is_idempotent_and_preserves_unresolved_and_history(self):
        call_command("load_foods", stdout=io.StringIO())
        original = Food.objects.get(name="Yuca cocida").kcal100
        call_command(
            "load_usda_catalog",
            apply=True,
            activate=True,
            stdout=io.StringIO(),
        )
        self.assertEqual(FoodImportRevision.objects.count(), 70)
        call_command(
            "load_usda_catalog",
            apply=True,
            activate=True,
            stdout=io.StringIO(),
        )
        self.assertEqual(FoodImportRevision.objects.count(), 70)
        self.assertEqual(Food.objects.count(), 90)
        food = Food.objects.get(name="Yuca cocida")
        self.assertIsNone(food.fdc_id)
        self.assertEqual(food.kcal100, original)
        self.assertEqual(
            sum(source_is_consistent(food) for food in Food.objects.all()), 70
        )
        self.assertFalse(Food.objects.exclude(validated_by="").exists())

    def test_bulk_catalog_does_not_downgrade_a_different_source_revision(self):
        call_command("load_usda_catalog", apply=True, stdout=io.StringIO())
        _, entries, _ = load_reference()
        entry = entries[0]
        payload = copy.deepcopy(entry["record"])
        next(row for row in payload["foodNutrients"] if row["nutrient"]["id"] == 1008)[
            "amount"
        ] += 1
        food = Food.objects.get(name=entry["name"])
        food, _, _ = import_food(
            payload,
            food_id=food.pk,
            state=entry["state"],
            confirm_description=entry["expected_description"],
            apply=True,
        )
        current = food.kcal100
        with self.assertRaisesMessage(
            CommandError, "fdc_catalog_existing_revision_requires_review"
        ):
            call_command("load_usda_catalog", apply=True, stdout=io.StringIO())
        food.refresh_from_db()
        self.assertEqual(food.kcal100, current)

    def test_reviewed_data_cancels_bulk_update_atomically(self):
        call_command("load_foods", stdout=io.StringIO())
        Food.objects.filter(name="Miel de abeja").update(
            validated_by="Revisión anterior"
        )
        with self.assertRaises(CommandError):
            call_command("load_usda_catalog", apply=True, stdout=io.StringIO())
        self.assertFalse(Food.objects.filter(fdc_id__isnull=False).exists())
        self.assertFalse(FoodImportRevision.objects.exists())


@override_settings(NUTRITION_REQUIRE_USDA_REFERENCE=True)
class StrictCatalogFlowTests(PlanScenarioTestCase):
    def setUp(self):
        super().setUp()
        call_command("load_foods", stdout=io.StringIO())
        call_command(
            "load_usda_catalog",
            apply=True,
            activate=True,
            stdout=io.StringIO(),
        )

    def test_only_consistent_sources_are_offered_and_manual_edits_are_excluded(self):
        self.assertEqual(len(services.food_items()), 70)
        food = Food.objects.get(name="Banano")
        food.sodium_mg100 = 999
        food.save()
        self.assertNotIn(food.pk, {food.id for food in services.food_items()})

    def test_renaming_a_source_does_not_turn_it_into_another_food(self):
        food = Food.objects.get(name="Banano")
        food.name = "Banano renombrado sin revisar"
        food.save(update_fields=["name"])
        self.assertNotIn(food.pk, {item.id for item in services.food_items()})

    def test_reference_variant_is_frozen_and_visible_to_the_client(self):
        self.ready(
            self.client_a,
            meal_slots=["breakfast", "mid_morning", "lunch", "snack", "dinner"],
        )
        with self.fake_ai(), self.captureOnCommitCallbacks(execute=True):
            job, _ = services.request_plan(
                self.client_a,
                KIND_NUTRITION,
                self.client_a,
                services.new_request_token(),
            )
        job.refresh_from_db()
        self.assertEqual(job.status, job.STATUS_SUCCEEDED, job.error_code)
        option = job.plan.nutrition.slots.first().options.first()
        self.assertTrue(
            all(
                row["source_qualifier"]
                for row in option.nutrient_snapshot["ingredients"]
            )
        )
        from apps.plans.nutrition import _option_view

        shown = _option_view(option)
        self.assertTrue(all(item["source_qualifier"] for item in shown["items"]))
        before = shown["items"][0]["source_qualifier"]
        Food.objects.filter(pk=shown["items"][0]["food"].pk).update(
            name="Nombre editado después del plan"
        )
        self.assertEqual(_option_view(option)["items"][0]["source_qualifier"], before)

    def test_three_goals_create_five_meals_with_frozen_usda_provenance(self):
        for client, goal in [
            (self.client_a, "weight_loss"),
            (self.client_b, "muscle_gain"),
            (self.client_free, "cardio"),
        ]:
            with self.subTest(goal=goal):
                client.training_goal = goal
                client.save(update_fields=["training_goal"])
                self.ready(
                    client,
                    training_goal=goal,
                    meal_slots=["breakfast", "mid_morning", "lunch", "snack", "dinner"],
                )
                with self.fake_ai() as fake, self.captureOnCommitCallbacks(
                    execute=True
                ):
                    job, _ = services.request_plan(
                        client, KIND_NUTRITION, client, services.new_request_token()
                    )
                job.refresh_from_db()
                self.assertEqual(job.status, job.STATUS_SUCCEEDED, job.error_code)
                self.assertEqual(job.plan.nutrition.slots.count(), 35)
                payload = fake.calls[0]["payload"]
                self.assertNotIn('allowed_foods', payload)
                self.assertNotIn('food_nutrient_units', payload)
                self.assertEqual(payload['schema_version'], 'weekly-menu/1')

                for slot in job.plan.nutrition.slots.all():
                    for option in slot.options.all():
                        self.assertTrue(
                            nutrient_tables.option_table(option)["source_coverage"][
                                "complete"
                            ]
                        )
                option = job.plan.nutrition.slots.first().options.first()
                before = nutrient_tables.option_table(option)
                food = option.items.first().food
                previous = food.sodium_mg100
                Food.objects.filter(pk=food.pk).update(sodium_mg100=987)
                self.assertEqual(nutrient_tables.option_table(option), before)
                Food.objects.filter(pk=food.pk).update(sodium_mg100=previous)
