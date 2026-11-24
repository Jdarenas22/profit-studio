"""Integración real unificada y política Free; proveedor sustituido, sin red."""

import copy
from types import SimpleNamespace
from unittest import mock
from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from apps.plans import ai
from apps.plans.ai.base import (
    AIResult,
    AIPermanentError,
    AIInvalidOutput,
    AITransientError,
)
from apps.plans.ai.policy import configuration_errors
from apps.plans.ai.runtime import (
    RuntimeGeminiClient,
    free_request,
    hydrate_meal,
    reserve_call,
)
from apps.plans.models import GeminiDailyUsage

FREE = dict(
    AI_PLANS_MODE="live",
    GEMINI_API_KEY="fictional-key",
    GEMINI_MODEL="gemini-3.5-flash-lite",
    GEMINI_ACCESS_TIER="free",
    GEMINI_FREE_TIER_CONFIRMED=True,
    GEMINI_BILLING_CONFIRMED=False,
    GEMINI_MAX_DAILY_CALLS=2,
)


def meal_payload():
    return {
        "task": "meal_slot",
        "schema_version": "meal-slot/1",
        "job_ref": "anonymous-job",
        "client": {"age": 38, "sex": "F", "goal": "private", "weight": 72},
        "history": {"private": "clinical"},
        "trainer_notes": "private@example.test",
        "slot": {"code": "lunch", "target_kcal": 750, "protein_g": 50},
        "options_requested": 1,
        "recipe_options": [
            {
                "name": "Pollo con arroz",
                "cuisine": "Colombia",
                "main_protein": "pollo",
                "preparation": "Servir los ingredientes.",
                "items": [{"food_id": 1, "grams": 173}, {"food_id": 2, "grams": 218}],
            }
        ],
    }


class FreePolicyTests(SimpleTestCase):
    def test_free_does_not_require_billing_or_synthetic_accounts(self):
        with override_settings(**FREE):
            self.assertFalse(configuration_errors(settings))
            self.assertIsInstance(ai.get_ai_client(), RuntimeGeminiClient)

    @override_settings(**FREE)
    def test_unconfirmed_paid_or_unknown_configuration_fails_closed(self):
        for change in (
            {"GEMINI_FREE_TIER_CONFIRMED": False},
            {"GEMINI_MODEL": "unknown-paid-model"},
            {"GEMINI_BILLING_CONFIRMED": True},
            {"GEMINI_ACCESS_TIER": "invalid"},
            {"GEMINI_MAX_DAILY_CALLS": 0},
        ):
            with self.subTest(change=change), override_settings(**change):
                self.assertTrue(configuration_errors(settings))
                with self.assertRaises(AIPermanentError):
                    ai.get_ai_client()

    def test_blank_mode_is_off_even_with_debug(self):
        with override_settings(AI_PLANS_MODE="", DEBUG=True):
            self.assertEqual(ai.ai_mode(), "off")

    def test_obsolete_demo_mode_is_not_an_execution_path(self):
        with override_settings(AI_PLANS_MODE="demo"):
            self.assertEqual(ai.ai_mode(), "off")
            with self.assertRaises(AIPermanentError):
                ai.get_ai_client()

    def test_free_meals_send_catalog_without_profile_or_personal_amounts(self):
        original = meal_payload()
        before = copy.deepcopy(original)
        _system, payload, schema = free_request("meal_slot", original, {})
        self.assertEqual(original, before)
        self.assertEqual(
            set(payload),
            {
                "task",
                "schema_version",
                "previous_errors",
                "slot_code",
                "options_requested",
                "recipe_options",
            },
        )
        self.assertNotIn("private", str(payload))
        self.assertNotIn("173", str(payload))
        self.assertNotIn("target_kcal", str(payload))
        self.assertNotIn("items", payload["recipe_options"][0])
        self.assertEqual(
            schema["properties"]["recipe_names"]["items"]["enum"], ["Pollo con arroz"]
        )
        hydrated = hydrate_meal({"recipe_names": ["Pollo con arroz"]}, original)
        self.assertEqual(
            hydrated["options"][0]["items"], original["recipe_options"][0]["items"]
        )

    def test_unknown_or_duplicate_model_selection_is_rejected(self):
        for data in (
            {"recipe_names": ["invented"]},
            {"recipe_names": ["Pollo con arroz"], "extra": 1},
            {"recipe_names": ["Pollo con arroz", "Pollo con arroz"]},
            {"recipe_names": [1]},
        ):
            with self.subTest(data=data), self.assertRaises(AIInvalidOutput):
                hydrate_meal(data, meal_payload())

    def test_free_exercise_omits_profile_notes_history_and_job_reference(self):
        payload = {
            "schema_version": "exercise-plan/1",
            "client": {"age": 45},
            "history": {"medical": "private"},
            "trainer_notes": "private text",
            "training": {
                "days_per_week": 3,
                "session_minutes": 45,
                "assigned_routine": {"private": 1},
            },
            "limits": {"sets_max": 3},
            "allowed_exercises": [{"id": 1, "name": "Sentadilla"}],
        }
        _system, out, _schema = free_request(
            "exercise_plan", payload, {"type": "object"}
        )
        self.assertNotIn("client", out)
        self.assertNotIn("private", str(out))
        self.assertEqual(out["training"], {"days_per_week": 3, "session_minutes": 45})


@override_settings(**FREE)
class RuntimeBudgetTests(TestCase):
    def test_budget_uses_application_database_and_never_exceeds_limit(self):
        reserve_call()
        reserve_call()
        with self.assertRaisesMessage(AIPermanentError, "ai_daily_call_limit"):
            reserve_call()
        self.assertEqual(GeminiDailyUsage.objects.get().calls, 2)

    def test_provider_failure_counts_and_does_not_fallback(self):
        client = RuntimeGeminiClient(api_key="fictional", model="gemini-3.5-flash-lite")
        with mock.patch(
            "apps.plans.ai.gemini.GeminiClient.generate_json",
            side_effect=AITransientError("ai_network"),
        ) as provider:
            with self.assertRaises(AITransientError):
                client.generate_json(
                    task="meal_slot",
                    system="private",
                    payload=meal_payload(),
                    schema={},
                    job_ref="job",
                )
        self.assertEqual(GeminiDailyUsage.objects.get().calls, 1)
        self.assertNotIn("private", str(provider.call_args.kwargs))

    def test_generated_selection_is_hydrated_from_local_quantities(self):
        client = RuntimeGeminiClient(api_key="fictional", model="gemini-3.5-flash-lite")
        with mock.patch(
            "apps.plans.ai.gemini.GeminiClient.generate_json",
            return_value=AIResult(
                data={"recipe_names": ["Pollo con arroz"]}, model="real-provider"
            ),
        ):
            result = client.generate_json(
                task="meal_slot",
                system="private",
                payload=meal_payload(),
                schema={},
                job_ref="job",
            )
        self.assertEqual(result.data["options"][0]["items"][0]["grams"], 173)
        self.assertEqual(result.model, "real-provider")
        self.assertFalse(client.is_simulated)

    def test_invalid_policy_never_reserves_or_calls_provider(self):
        with override_settings(GEMINI_FREE_TIER_CONFIRMED=False), mock.patch(
            "apps.plans.ai.gemini.GeminiClient.generate_json"
        ) as provider:
            with self.assertRaises(AIPermanentError):
                reserve_call()
            provider.assert_not_called()
        self.assertFalse(GeminiDailyUsage.objects.exists())


from .test_nutrition import NutritionScenarioTestCase
from apps.plans import services, nutrient_tables


class NormalFreeFlowTests(NutritionScenarioTestCase):
    def test_regular_account_generates_five_meals_with_free_privacy_and_local_portions(
        self,
    ):
        import io
        from apps.plans.models import Food

        # Las etiquetas profesionales pertenecen a fixtures de otras pruebas, no a fuentes USDA.
        Food.objects.update(validated_by="")
        call_command(
            "load_usda_catalog", apply=True, activate=True, stdout=io.StringIO()
        )
        self.ready(
            self.client_a,
            meal_slots=["breakfast", "mid_morning", "lunch", "snack", "dinner"],
        )
        calls = []

        def provider(**kwargs):
            payload = kwargs["payload"]
            calls.append(payload)
            from apps.plans.rules.recipes import select_options

            # El sustituto usa solo ingredientes públicos, igual que la solicitud Free.
            catalog = [
                {
                    **recipe,
                    "items": [{"food_id": value} for value in recipe["food_ids"]],
                }
                for recipe in payload["recipe_options"]
            ]
            names = [
                row["name"]
                for row in select_options(catalog, payload["options_requested"])
            ]
            return AIResult(data={"recipe_names": names}, model="gemini-3.5-flash-lite")

        options = {
            **FREE,
            "GEMINI_MAX_DAILY_CALLS": 30,
            "NUTRITION_REQUIRE_USDA_REFERENCE": True,
        }
        with override_settings(**options), mock.patch(
            "apps.plans.ai.gemini.GeminiClient.generate_json", side_effect=provider
        ), self.captureOnCommitCallbacks(execute=True):
            job, _ = services.request_plan(
                self.client_a, "nutrition", self.client_a, services.new_request_token()
            )
        job.refresh_from_db()
        self.assertEqual(job.status, job.STATUS_SUCCEEDED, job.error_code)
        self.assertFalse(job.plan.is_simulated)
        self.assertEqual(len(calls), 5)
        self.assertEqual(GeminiDailyUsage.objects.get().calls, 5)
        for payload in calls:
            self.assertNotIn("client", payload)
            self.assertNotIn("history", payload)
            self.assertNotIn("target_kcal", str(payload))
            self.assertNotIn("grams", str(payload))
        for slot in job.plan.nutrition.slots.all():
            self.assertEqual(slot.options.count(), 3)
            for option in slot.options.all():
                self.assertTrue(
                    nutrient_tables.option_table(option)["source_coverage"]["complete"]
                )
