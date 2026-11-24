"""Resumen de rutina asignada para el flujo normal; sin red."""

from apps.plans.training_context import assigned_training_summary
from .test_nutrition import NutritionScenarioTestCase


class AssignedRoutineTests(NutritionScenarioTestCase):
    def test_only_own_active_routine_without_names_or_notes(self):
        self.routine_a.name = "private routine name"
        self.routine_a.notes = "private free text"
        self.routine_a.save()
        result = assigned_training_summary(self.client_a)
        self.assertEqual(result["days_per_cycle"], 1)
        self.assertEqual(result["days"][0]["total_sets"], 3)
        self.assertNotIn("private", str(result))
        self.assertNotIn(self.client_a.username, str(result))
        self.routine_a.is_active = False
        self.routine_a.save()
        self.assertFalse(assigned_training_summary(self.client_a)["assigned"])

    def test_five_meal_calls_keep_personal_training_summary_local(self):
        self.ready(
            self.client_a,
            meal_slots=["breakfast", "mid_morning", "lunch", "snack", "dinner"],
        )
        job, fake = self.request_nutrition(self.client_a)
        self.assertEqual(job.status, job.STATUS_SUCCEEDED, job.error_code)
        self.assertEqual(len(fake.calls), 5)
        summary = assigned_training_summary(self.client_a)
        self.assertTrue(summary['assigned'])
        self.assertEqual(summary['days_per_cycle'], 1)
        for call in fake.calls:
            self.assertNotIn('training', call['payload'])
            self.assertEqual(call['payload']['days'], 7)
