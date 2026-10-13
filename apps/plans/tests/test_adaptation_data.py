"""Capa de datos de la adaptación (paso 2 de docs/contracts/plan-ia-adaptacion.md): lectura del
historial, bloque `history` en el pedido a la IA, exclusiones, resumen del trabajo y aislamiento de
fallos. Sin red: siempre `FakeAIClient`."""
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from apps.assessments.models import BodyMeasurement
from apps.health import dates as health_dates
from apps.plans import adaptation, nutrition, services
from apps.plans.models import (
    KIND_EXERCISE, Food, MealChoice, MealOption, MealOptionItem, Plan, PlanGenerationJob,
)
from apps.routines.models import Routine, RoutineDay, RoutineDayLog, RoutineExercise

from .helpers import NutritionScenarioTestCase, PlanScenarioTestCase

BASE_KEYS = {'task', 'schema_version', 'job_ref', 'client', 'training', 'limits', 'allowed_exercises',
             'trainer_notes', 'previous_errors'}


def days_ago(n):
    return timezone.now() - timedelta(days=n)


class HistoryMixin:
    """Atajos para fabricar historial de una clienta."""

    def approve_exercise(self, client, days=30):
        self.ready(client)
        job, _fake = self.request(client)
        plan = job.plan
        Plan.objects.filter(pk=plan.pk).update(is_simulated=False)
        plan.refresh_from_db()
        plan = services.approve_plan(plan, self.boss)
        Plan.objects.filter(pk=plan.pk).update(approved_at=days_ago(days))
        plan.refresh_from_db()
        return plan

    def log_day(self, client, day, ago):
        log = RoutineDayLog.objects.create(user=client, routine_day=day)
        RoutineDayLog.objects.filter(pk=log.pk).update(completed_at=health_dates.today() - timedelta(days=ago))

    def make_reviewed_exercise_plan(self, client, *, version, status, proposed, kept, approved_days_ago):
        """Plan ya revisado: la IA propuso `proposed` y la rutina final solo tiene `kept`."""
        routine = Routine.objects.create(name=f'R{version}', user=client, trainer=self.trainer_a, is_active=False)
        day = RoutineDay.objects.create(routine=routine, name='Día 1', order=0)
        for order, exercise in enumerate(kept):
            RoutineExercise.objects.create(day=day, exercise=exercise, sets=3, reps='10', rest_seconds=60,
                                           order=order)
        return Plan.objects.create(
            client=client, kind=KIND_EXERCISE, version=version, status=status, routine=routine,
            approved_at=days_ago(approved_days_ago), ai_item_ids=sorted(e.pk for e in proposed))


@override_settings(AI_PLANS_ADAPTATION=True)
class ExerciseAdaptationTests(HistoryMixin, PlanScenarioTestCase):
    def test_no_history_means_no_history_block_but_the_summary_is_saved(self):
        self.ready(self.client_a)
        job, fake = self.request(self.client_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        self.assertEqual(set(fake.calls[0]['payload']), BASE_KEYS)
        self.assertFalse(job.adaptation_summary['ready'])
        self.assertFalse(job.adaptation_summary['history_sent'])

    def test_adherence_goes_to_the_ai_and_to_the_summary(self):
        plan = self.approve_exercise(self.client_a, days=30)
        day = plan.routine.days.first()
        for ago in (3, 10):
            self.log_day(self.client_a, day, ago)
        job, fake = self.request(self.client_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        payload = fake.calls[0]['payload']
        self.assertEqual(set(payload), BASE_KEYS | {'history'})
        self.assertEqual(payload['history']['training']['adherence'], 'low')
        self.assertEqual(job.adaptation_summary['adherence'], 'low')
        self.assertTrue(job.adaptation_summary['history_sent'])
        self.assertEqual(job.adaptation_summary['version'], 'adaptation/1')

    def test_history_contains_nothing_identifying(self):
        plan = self.approve_exercise(self.client_a, days=30)
        self.log_day(self.client_a, plan.routine.days.first(), 3)
        job, fake = self.request(self.client_a)
        sent = json.dumps(fake.calls[0]['payload']['history'], ensure_ascii=False)
        for secret in (self.client_a.username, self.client_a.email, self.client_a.first_name,
                       self.client_a.last_name, self.trainer_a.username):
            self.assertNotIn(secret, sent)
        summary = json.dumps(job.adaptation_summary, ensure_ascii=False)
        self.assertNotIn(plan.routine.days.first().name, summary)

    def test_less_than_14_days_of_history_sends_nothing(self):
        self.approve_exercise(self.client_a, days=10)
        job, fake = self.request(self.client_a)
        self.assertNotIn('history', fake.calls[0]['payload'])
        self.assertFalse(job.adaptation_summary['ready'])

    def test_exercises_the_trainer_keeps_removing_are_not_offered_again(self):
        removed, keep_a, keep_b = self.tagged[0], self.tagged[1], self.tagged[2]
        for version, status, ago in ((1, Plan.STATUS_SUPERSEDED, 50), (2, Plan.STATUS_APPROVED, 30)):
            self.make_reviewed_exercise_plan(
                self.client_a, version=version, status=status, proposed=[removed, keep_a, keep_b],
                kept=[keep_a, keep_b], approved_days_ago=ago)
        self.ready(self.client_a)
        job, fake = self.request(self.client_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        payload = fake.calls[0]['payload']
        offered = {item['id'] for item in payload['allowed_exercises']}
        self.assertNotIn(removed.pk, offered)
        self.assertTrue({keep_a.pk, keep_b.pk} <= offered)
        self.assertEqual(payload['history']['trainer_edits']['removed_exercise_ids'], [removed.pk])
        self.assertEqual(job.adaptation_summary['excluded_exercises'], 1)
        # el borrador generado no usa el ejercicio quitado
        self.assertFalse(RoutineExercise.objects.filter(day__routine=job.plan.routine, exercise=removed).exists())

    def test_one_removal_is_not_a_pattern(self):
        removed, keep = self.tagged[0], self.tagged[1]
        self.make_reviewed_exercise_plan(self.client_a, version=1, status=Plan.STATUS_APPROVED,
                                         proposed=[removed, keep], kept=[keep], approved_days_ago=30)
        self.ready(self.client_a)
        _job, fake = self.request(self.client_a)
        offered = {item['id'] for item in fake.calls[0]['payload']['allowed_exercises']}
        self.assertIn(removed.pk, offered)

    def test_the_catalog_is_never_cut_below_the_minimum(self):
        # 11 ejercicios permitidos (10 etiquetados + 1 sin etiquetar); quitar 6 dejaría 5 (< 6): ninguno se excluye
        removed = self.tagged[:6]
        for version, status, ago in ((1, Plan.STATUS_SUPERSEDED, 50), (2, Plan.STATUS_APPROVED, 30)):
            self.make_reviewed_exercise_plan(
                self.client_a, version=version, status=status, proposed=list(self.tagged),
                kept=list(self.tagged[6:]), approved_days_ago=ago)
        self.ready(self.client_a)
        job, fake = self.request(self.client_a)
        offered = {item['id'] for item in fake.calls[0]['payload']['allowed_exercises']}
        self.assertTrue({e.pk for e in removed} <= offered)
        self.assertEqual(job.adaptation_summary['excluded_exercises'], 0)

    def test_the_history_never_adds_exercises_outside_the_safe_catalog(self):
        self.ready(self.client_a, injuries=['knee'])
        safe = set(services.assess(self.client_a, KIND_EXERCISE, self.client_a, check_quota=False).catalog.allowed_ids)
        self.assertTrue(safe)
        removed = self.tagged[0]
        for version, status, ago in ((1, Plan.STATUS_SUPERSEDED, 50), (2, Plan.STATUS_APPROVED, 30)):
            self.make_reviewed_exercise_plan(self.client_a, version=version, status=status,
                                             proposed=[removed, self.tagged[1]], kept=[self.tagged[1]],
                                             approved_days_ago=ago)
        _job, fake = self.request(self.client_a)
        offered = {item['id'] for item in fake.calls[0]['payload']['allowed_exercises']}
        self.assertTrue(offered <= safe)

    def test_without_current_consent_nothing_is_used(self):
        job = SimpleNamespace(client=self.client_b, kind=KIND_EXERCISE, pk=1)
        assessment = SimpleNamespace(profile=object(), catalog=None, foods=None)
        self.assertIsNone(adaptation.for_job(job, assessment))

    def test_a_failure_in_the_adaptation_does_not_break_the_plan(self):
        self.ready(self.client_a)
        with mock.patch('apps.plans.adaptation.collect', side_effect=RuntimeError('boom')), \
                self.assertLogs('apps.plans.adaptation', level='WARNING') as logs:
            job, fake = self.request(self.client_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        self.assertNotIn('history', fake.calls[0]['payload'])
        self.assertIn('RuntimeError', '\n'.join(logs.output))
        self.assertNotIn('boom', '\n'.join(logs.output))
        self.assertEqual(job.adaptation_summary, {})

    def test_the_draft_remembers_which_exercises_the_ai_proposed(self):
        self.ready(self.client_a)
        job, _fake = self.request(self.client_a)
        used = set(RoutineExercise.objects.filter(day__routine=job.plan.routine).values_list('exercise_id', flat=True))
        self.assertEqual(set(job.plan.ai_item_ids), used)
        self.assertEqual(job.plan.ai_item_ids, sorted(job.plan.ai_item_ids))


@override_settings(AI_PLANS_ADAPTATION=False)
class AdaptationOffTests(HistoryMixin, PlanScenarioTestCase):
    """Con el interruptor apagado (valor por defecto) el pedido es idéntico al de siempre."""

    def test_nothing_changes_when_disabled(self):
        self.assertFalse(adaptation.enabled())
        plan = self.approve_exercise(self.client_a, days=30)
        self.log_day(self.client_a, plan.routine.days.first(), 3)
        job, fake = self.request(self.client_a)
        self.assertEqual(set(fake.calls[0]['payload']), BASE_KEYS)
        self.assertEqual(job.adaptation_summary, {})


@override_settings(AI_PLANS_ADAPTATION=True)
class CollectTests(HistoryMixin, PlanScenarioTestCase):
    def collect(self, client=None):
        client = client or self.client_a
        return adaptation.collect(client, self.ready(client))

    def test_empty_history(self):
        data = self.collect()
        self.assertIsNone(data.history_since)
        self.assertEqual((data.training_logs, data.completed_meals, data.discarded_options,
                          data.trainer_removals), ((), (), (), ()))

    def test_history_starts_at_the_first_approval(self):
        self.make_reviewed_exercise_plan(self.client_a, version=1, status=Plan.STATUS_SUPERSEDED,
                                         proposed=[], kept=[self.tagged[0]], approved_days_ago=60)
        self.make_reviewed_exercise_plan(self.client_a, version=2, status=Plan.STATUS_APPROVED,
                                         proposed=[], kept=[self.tagged[0]], approved_days_ago=5)
        self.assertEqual(self.collect().history_since, health_dates.today() - timedelta(days=60))

    def test_logs_belong_only_to_the_client_and_the_window(self):
        plan = self.approve_exercise(self.client_a, days=30)
        day = plan.routine.days.first()
        self.log_day(self.client_a, day, 2)
        self.log_day(self.client_a, day, 60)                # fuera de la ventana
        data = self.collect()
        self.assertEqual([log.day_id for log in data.training_logs], [day.pk])
        self.assertEqual(self.collect(self.client_b).training_logs, ())

    def test_weights_skip_measurements_flagged_for_review(self):
        self.ready(self.client_a)
        good = BodyMeasurement.objects.create(user=self.client_a, trainer=self.trainer_a, weight=70)
        bad = BodyMeasurement.objects.create(user=self.client_a, trainer=self.trainer_a, weight=99,
                                             needs_review=True)
        BodyMeasurement.objects.filter(pk=good.pk).update(date=health_dates.today() - timedelta(days=20))
        BodyMeasurement.objects.filter(pk=bad.pk).update(date=health_dates.today() - timedelta(days=3))
        data = adaptation.collect(self.client_a, self.ready(self.client_a))
        self.assertIn(70.0, [p.kg for p in data.weights])
        self.assertNotIn(99.0, [p.kg for p in data.weights])


@override_settings(AI_PLANS_ADAPTATION=True)
class NutritionAdaptationTests(NutritionScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.plan = self.approved_nutrition(self.client_a)
        Plan.objects.filter(pk=self.plan.pk).update(approved_at=days_ago(30))
        self.slot = self.plan.nutrition.slots.get(code='lunch')
        self.food = Food.objects.exclude(
            pk__in=MealOptionItem.objects.filter(option__slot__nutrition_plan__plan=self.plan).values('food_id')).first()

    def discard_with_food(self, option):
        MealOptionItem.objects.create(option=option, food=self.food, grams=60, order=9)
        with self.fake_ai() as fake, self.captureOnCommitCallbacks(execute=True):
            job, _ = nutrition.request_alternative(self.client_a, self.slot.pk, option.pk, services.new_request_token())
        job.refresh_from_db()
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        return job, fake

    def test_a_food_the_client_keeps_discarding_stops_being_offered(self):
        first, second = self.slot.options.get(position=1), self.slot.options.get(position=2)
        self.discard_with_food(first)
        job, fake = self.discard_with_food(second)           # 2.º descarte: ya hay patrón para el siguiente
        with self.fake_ai() as fake, self.captureOnCommitCallbacks(execute=True):
            job, _created = services.request_plan(
                self.client_a, 'nutrition', self.client_a, services.new_request_token())
        job.refresh_from_db()
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        payload = fake.calls[0]['payload']
        avoid = payload['history']['nutrition']['avoid_food_ids']
        self.assertIn(self.food.pk, avoid)                    # estaba en los dos descartes
        offered = {row['id'] for row in payload['allowed_foods']}
        self.assertFalse(offered & set(avoid))                 # nada de lo que evita se le vuelve a ofrecer
        self.assertEqual(job.adaptation_summary['excluded_foods'], len(avoid))

    def test_a_reverted_alternative_is_not_a_discard(self):
        option = self.slot.options.get(position=1)
        self.discard_with_food(option)
        new = self.slot.options.get(position=1, is_active=True)
        trainer = self.boss
        nutrition.review_option(self.plan, new, trainer, 'revert')
        self.assertEqual(adaptation._discarded_options(self.client_a), ())

    def test_completed_meals_become_liked_foods(self):
        option = self.slot.options.get(position=1)
        for ago in (1, 2, 3):
            MealChoice.objects.create(client=self.client_a, slot=self.slot, option=option,
                                      date=health_dates.today() - timedelta(days=ago), completed_at=timezone.now())
        meals = adaptation._completed_meals(self.client_a, health_dates.today())
        self.assertEqual(len(meals), 3)
        self.assertEqual(set(meals[0]), set(option.items.values_list('food_id', flat=True)))

    def test_the_client_discard_is_not_counted_as_a_trainer_correction(self):
        option = self.slot.options.get(position=1)
        self.discard_with_food(option)
        removals = adaptation._trainer_removals(self.client_a)
        Plan.objects.filter(pk=self.plan.pk).update(status=Plan.STATUS_APPROVED)
        self.assertTrue(all(not r.food_ids for r in removals))

    def test_the_draft_remembers_which_foods_the_ai_proposed(self):
        foods = set(MealOptionItem.objects.filter(option__slot__nutrition_plan__plan=self.plan,
                                                   option__origin=MealOption.ORIGIN_AI).values_list('food_id', flat=True))
        self.assertEqual(set(self.plan.ai_item_ids), foods)


@override_settings(AI_PLANS_ADAPTATION=True)
class PanelTests(HistoryMixin, PlanScenarioTestCase):
    """Panel "Qué se tuvo en cuenta" de la pantalla del plan (paso 3)."""

    def set_summary(self, job, **summary):
        PlanGenerationJob.objects.filter(pk=job.pk).update(adaptation_summary=summary)
        job.plan.refresh_from_db()

    def test_no_panel_when_the_job_did_not_use_the_adaptation(self):
        self.ready(self.client_a)
        with override_settings(AI_PLANS_ADAPTATION=False):
            job, _fake = self.request(self.client_a)
        self.assertIsNone(adaptation.panel_for_plan(job.plan))

    def test_cold_start_says_there_is_not_enough_history(self):
        self.ready(self.client_a)
        job, _fake = self.request(self.client_a)
        panel = adaptation.panel_for_plan(job.plan)
        self.assertEqual((panel['ready'], panel['items']), (False, []))
        self.assertTrue(panel['footnote'])

    def test_real_history_becomes_plain_sentences(self):
        plan = self.approve_exercise(self.client_a, days=30)
        self.log_day(self.client_a, plan.routine.days.first(), 3)
        job, _fake = self.request(self.client_a)
        panel = adaptation.panel_for_plan(job.plan)
        self.assertTrue(panel['ready'])
        self.assertIn('Constancia baja', panel['items'][0]['text'])
        self.assertEqual(panel['items'][0]['tone'], 'warning')
        self.assertFalse(panel['calorie_review'])

    def test_counts_use_singular_and_plural(self):
        self.ready(self.client_a)
        job, _fake = self.request(self.client_a)
        self.set_summary(job, ready=True, weeks_of_data=4, skipped_days=1, excluded_exercises=2)
        texts = [item['text'] for item in adaptation.panel_for_plan(job.plan)['items']]
        self.assertIn('1 día de su rutina casi nunca se hace (menos del 40 %).', texts)
        self.assertIn('2 ejercicios dejaron de ofrecerse a la IA por esas correcciones.', texts)

    def test_calorie_review_is_only_for_nutrition_plans(self):
        self.ready(self.client_a)
        job, _fake = self.request(self.client_a)
        self.set_summary(job, ready=True, weeks_of_data=4, suggest_calorie_review=True)
        self.assertFalse(adaptation.panel_for_plan(job.plan)['calorie_review'])

    def test_the_panel_has_no_personal_data(self):
        plan = self.approve_exercise(self.client_a, days=30)
        self.log_day(self.client_a, plan.routine.days.first(), 3)
        job, _fake = self.request(self.client_a)
        text = json.dumps(adaptation.panel_for_plan(job.plan), ensure_ascii=False)
        for secret in (self.client_a.username, self.client_a.email, self.client_a.first_name,
                       plan.routine.days.first().name):
            self.assertNotIn(secret, text)

    def test_the_trainer_screen_gets_the_panel_and_the_client_never_does(self):
        from django.urls import reverse
        plan = self.approve_exercise(self.client_a, days=30)
        self.log_day(self.client_a, plan.routine.days.first(), 3)
        job, _fake = self.request(self.client_a)
        self.login(self.trainer_a)
        response = self.client.get(reverse('trainer_plan_detail', args=[job.plan.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context['adaptation'])
        self.assertContains(response, 'Qué se tuvo en cuenta')
        self.assertContains(response, 'Constancia baja')
        self.assertNotContains(response, 'Sugerencia: revisa el ajuste de calorías')   # solo alimentación
        self.login(self.client_a)
        self.assertNotEqual(self.client.get(reverse('trainer_plan_detail', args=[job.plan.pk])).status_code, 200)


    def test_the_screen_has_no_panel_when_the_adaptation_was_not_used(self):
        from django.urls import reverse
        self.ready(self.client_a)
        with override_settings(AI_PLANS_ADAPTATION=False):
            job, _fake = self.request(self.client_a)
        self.login(self.trainer_a)
        response = self.client.get(reverse('trainer_plan_detail', args=[job.plan.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['adaptation'])
        self.assertNotContains(response, 'Qué se tuvo en cuenta')

    def test_the_cold_start_message_renders(self):
        from django.urls import reverse
        self.ready(self.client_a)
        job, _fake = self.request(self.client_a)
        self.login(self.trainer_a)
        response = self.client.get(reverse('trainer_plan_detail', args=[job.plan.pk]))
        self.assertContains(response, 'Aún no hay historial suficiente')


@override_settings(AI_PLANS_ADAPTATION=True)
class NutritionPanelTests(NutritionScenarioTestCase):
    def test_the_calorie_suggestion_shows_on_a_nutrition_plan_and_changes_no_calories(self):
        from django.urls import reverse
        plan = self.nutrition_draft(self.client_a)
        kcal = plan.nutrition.kcal_target
        job = plan.jobs.get()
        PlanGenerationJob.objects.filter(pk=job.pk).update(adaptation_summary={
            'ready': True, 'weeks_of_data': 5, 'adherence': 'high', 'progress': 'stalled',
            'suggest_calorie_review': True})
        self.login(self.boss)
        response = self.client.get(reverse('trainer_plan_detail', args=[plan.pk]))
        self.assertContains(response, 'Sugerencia: revisa el ajuste de calorías')
        self.assertNotContains(response, 'Constancia alta')       # la constancia es del plan de ejercicio
        plan.nutrition.refresh_from_db()
        self.assertEqual(plan.nutrition.kcal_target, kcal)


class PanelByKindTests(HistoryMixin, PlanScenarioTestCase):
    """Cada tipo de plan muestra solo las frases que le corresponden."""
    SUMMARY = dict(ready=True, weeks_of_data=4, adherence='low', skipped_days=2, liked_foods=3, avoided_foods=1,
                   removed_exercises=1, removed_foods=2, excluded_exercises=1, excluded_foods=2, progress='stalled')

    def texts(self, kind):
        from apps.plans.models import KIND_NUTRITION
        self.ready(self.client_a)
        job, _fake = self.request(self.client_a)
        plan = job.plan
        Plan.objects.filter(pk=plan.pk).update(kind=kind)
        plan.refresh_from_db()
        PlanGenerationJob.objects.filter(pk=job.pk).update(kind=kind, adaptation_summary=self.SUMMARY)
        return ' | '.join(item['text'] for item in adaptation.panel_for_plan(plan)['items'])

    def test_the_exercise_panel_has_no_food_lines(self):
        text = self.texts('exercise')
        for expected in ('Constancia baja', 'días de su rutina', 'ejercicios que se quitaron', 'ejercicio dejó de ofrecerse',
                         'Su peso lleva semanas'):
            self.assertIn(expected, text)
        for unexpected in ('alimento', 'Alimento'):
            self.assertNotIn(unexpected, text)

    def test_the_nutrition_panel_has_no_training_lines(self):
        text = self.texts('nutrition')
        for expected in ('alimentos que elige', 'alimento que descarta', 'alimentos que se quitaron',
                         'alimentos dejaron de ofrecerse', 'Su peso lleva semanas'):
            self.assertIn(expected, text)
        for unexpected in ('Constancia', 'rutina', 'ejercicio'):
            self.assertNotIn(unexpected, text)
