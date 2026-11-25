"""Clonar a borrador (fase D, D-03; docs/contracts/plan-ia-fase-d.md, sección 7).

Qué se prueba: autorización e interruptores, qué se copia (ejercicio y alimentación), que la clienta no
ve la copia, estados, procedencia (ficha obsoleta, advertencias recalculadas, `ai_item_ids` vacío),
incompatibilidades con la ficha de hoy, idempotencia (doble clic) y que no hay IA ni correos.
"""
import uuid
from unittest import mock

from django.core import mail
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.permissions import INACTIVE_CLIENT_MESSAGE
from apps.assessments.models import BodyMeasurement
from apps.health.tests.helpers import stub_missing_templates
from apps.plans import adaptation, cloning, nutrition, services, texts
from apps.plans.models import (
    KIND_EXERCISE, KIND_NUTRITION, Food, MealChoice, MealOption, MealOptionItem, MealSlot, NutritionPlan, Plan,
    PlanEvent, PlanGenerationJob,
)
from apps.routines.models import Routine, RoutineDay, RoutineDayLog, RoutineExercise

from .helpers import NutritionScenarioTestCase


def messages_of(response):
    """Solo los mensajes que ESTA petición dejó (los de peticiones anteriores siguen en la sesión
    mientras nadie pinte una pantalla)."""
    return [str(message) for message in response.wsgi_request._messages._queued_messages]


def routine_rows(routine):
    return [(day.name, day.order, [(i.exercise_id, i.sets, i.reps, i.rest_seconds, i.observations, i.order)
                                   for i in day.exercises.all()]) for day in routine.days.all()]


def nutrition_rows(plan):
    rows = []
    for slot in plan.nutrition.slots.all():
        options = slot.options.filter(is_active=True).order_by('position')
        rows.append((slot.code, slot.name, slot.order, slot.time_hint, slot.kcal_target, slot.protein_g,
                     slot.carbs_g, slot.fat_g,
                     [(o.position, o.name, o.preparation, o.kcal, o.protein_g, o.carbs_g, o.fat_g, o.origin,
                       o.out_of_band, [(i.food_id, i.grams, i.order) for i in o.items.all()]) for o in options]))
    return rows


def counts():
    """Cuántas filas hay en cada tabla que toca clonar (para comprobar que un rechazo no deja nada)."""
    return tuple(model.objects.count() for model in (
        Plan, PlanEvent, Routine, RoutineDay, RoutineExercise, NutritionPlan, MealSlot, MealOption,
        MealOptionItem))


@stub_missing_templates()
class CloneTestCase(NutritionScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.ready(self.client_a)

    def clone(self, plan, user=None, **data):
        self.login(user or self.trainer_a)
        return self.client.post(reverse('trainer_plan_clone', args=[plan.pk]), data)

    def newest(self, client=None, kind=None):
        plans = Plan.objects.filter(client=client or self.client_a)
        return (plans.filter(kind=kind) if kind else plans).order_by('-pk').first()

    def exercise_pair(self):
        """Plan de ejercicio aprobado de client_a, con una observación para comprobar la copia."""
        plan = self.make_exercise(self.client_a, approve=True)
        item = RoutineExercise.objects.filter(day__routine=plan.routine).first()
        RoutineExercise.objects.filter(pk=item.pk).update(observations='Mantén la espalda recta', reps='8-10')
        return plan

    def detail_context(self, plan, user=None):
        self.login(user or self.trainer_a)
        response = self.client.get(reverse('trainer_plan_detail', args=[plan.pk]))
        self.assertEqual(response.status_code, 200)
        return response.context


class CloneAuthorizationTests(CloneTestCase):
    def setUp(self):
        super().setUp()
        self.plan = self.exercise_pair()
        self.url = reverse('trainer_plan_clone', args=[self.plan.pk])

    def test_anonymous_goes_to_login_client_gets_403_other_trainer_404(self):
        before = counts()
        self.client.logout()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])
        self.assertEqual(self.clone(self.plan, self.client_a).status_code, 403)
        self.assertEqual(self.clone(self.plan, self.trainer_b).status_code, 404)
        self.assertEqual(counts(), before)

    def test_assigned_trainer_and_superuser_can_clone(self):
        for user in (self.trainer_a, self.boss):
            with self.subTest(user=user.username):
                Plan.objects.filter(parent=self.plan).delete()
                response = self.clone(self.plan, user)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(self.newest().reviewed_by, user)

    def test_unassigned_client_is_only_reachable_by_the_superuser(self):
        self.ready(self.client_free)
        plan = self.make_exercise(self.client_free, approve=True)
        self.assertEqual(self.clone(plan, self.trainer_a).status_code, 404)
        self.assertEqual(self.clone(plan, self.boss).status_code, 302)

    def test_get_is_not_allowed_and_csrf_is_required(self):
        self.login(self.trainer_a)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.trainer_a, backend='django.contrib.auth.backends.ModelBackend')
        before = counts()
        self.assertEqual(strict.post(self.url).status_code, 403)
        self.assertEqual(counts(), before)

    def test_feature_off_is_a_404_for_each_switch_and_responses_are_no_store(self):
        before = counts()
        for label, switch in (('salud apagada', {'HEALTH_FEATURES_ENABLED': False}),
                              ('IA apagada', {'AI_PLANS_MODE': 'off'})):
            with self.subTest(label), override_settings(**switch):
                self.assertEqual(self.clone(self.plan).status_code, 404)
        self.assertEqual(counts(), before)
        response = self.clone(self.plan)
        self.assertEqual(response.status_code, 302)
        self.assertIn('no-store', response['Cache-Control'])

    def test_erased_client_is_a_404_and_an_inactive_one_is_refused(self):
        before = counts()
        User.objects.filter(pk=self.client_a.pk).update(is_active=False, deactivated_at=timezone.now())
        response = self.clone(self.plan)
        self.assertRedirects(response, reverse('trainer_plan_detail', args=[self.plan.pk]),
                             fetch_redirect_response=False)
        self.assertEqual(messages_of(response), [INACTIVE_CLIENT_MESSAGE])
        self.assertEqual(counts(), before)
        User.objects.filter(pk=self.client_a.pk).update(is_active=False, erased_at=timezone.now())
        self.assertEqual(self.clone(self.plan).status_code, 404)
        self.assertEqual(counts(), before)


class CloneExerciseTests(CloneTestCase):
    def setUp(self):
        super().setUp()
        self.plan = self.exercise_pair()

    def test_copies_the_routine_and_leaves_the_original_alone(self):
        original_rows = routine_rows(self.plan.routine)
        with self.fake_ai() as fake:
            before_mail = len(mail.outbox)
            response = self.clone(self.plan, reason='Subir el volumen')
            self.assertEqual(fake.calls, [])                       # no llama a la IA
            self.assertEqual(len(mail.outbox), before_mail)        # ni manda correos
        new = self.newest()
        self.assertRedirects(response, reverse('trainer_plan_detail', args=[new.pk]), fetch_redirect_response=False)
        self.assertEqual(new.status, Plan.STATUS_IN_REVIEW)
        self.assertEqual((new.kind, new.version, new.parent_id), (KIND_EXERCISE, self.plan.version + 1, self.plan.pk))
        self.assertEqual(new.reviewed_by, self.trainer_a)
        self.assertFalse(new.routine.is_active)
        self.assertNotEqual(new.routine_id, self.plan.routine_id)
        self.assertEqual((new.routine.name, new.routine.notes), (self.plan.routine.name, self.plan.routine.notes))
        self.assertEqual(new.routine.trainer, self.trainer_a)
        self.assertEqual(routine_rows(new.routine), original_rows)
        self.assertIn('Mantén la espalda recta', str(routine_rows(new.routine)))
        # El original no cambió
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, Plan.STATUS_APPROVED)
        self.assertTrue(self.plan.routine.is_active)
        self.assertEqual(routine_rows(self.plan.routine), original_rows)
        for name in ('approved_by', 'approved_at', 'valid_until'):
            self.assertIsNone(getattr(new, name))
        self.assertEqual((new.note_for_client, new.reviewer_credential), ('', ''))
        self.assertEqual(messages_of(response), [
            f'Creamos la versión {new.version} como borrador. La clienta sigue viendo la versión '
            f'{self.plan.version} hasta que apruebes esta.'])

    def test_one_event_with_the_reason_and_provenance_is_kept(self):
        self.clone(self.plan, reason='  Subir   el volumen ')
        new = self.newest()
        events = list(new.events.all())
        self.assertEqual([e.action for e in events], [PlanEvent.ACTION_CLONED])
        self.assertEqual(events[0].actor, self.trainer_a)
        self.assertEqual(events[0].note, f'Copia de la versión {self.plan.version}. Motivo: Subir el volumen')
        self.assertFalse(PlanEvent.objects.filter(plan=self.plan, action=PlanEvent.ACTION_CLONED).exists())
        self.assertEqual((new.health_profile_id, new.profile_version, new.measurement_id),
                         (self.plan.health_profile_id, self.plan.profile_version, self.plan.measurement_id))
        self.assertEqual((new.model_name, new.prompt_version, new.ruleset_version),
                         (self.plan.model_name, self.plan.prompt_version, self.plan.ruleset_version))

    def test_the_client_never_sees_the_copy(self):
        self.clone(self.plan)
        new = self.newest()
        day = new.routine.days.first()
        self.login(self.client_a)
        page = self.client.get(reverse('member_routine'))
        shown = [routine.pk for routine in page.context['routines']]
        self.assertIn(self.plan.routine_id, shown)
        self.assertNotIn(new.routine_id, shown)
        self.assertEqual(self.client.get(reverse('member_plans')).context['exercise_plan'], self.plan)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)
        self.assertEqual(self.client.post(reverse('mark_day_complete', args=[day.pk])).status_code, 404)
        self.assertFalse(RoutineDayLog.objects.filter(routine_day=day).exists())

    def test_clone_edit_approve_swaps_the_versions(self):
        self.clone(self.plan)
        new = self.newest()
        extra = RoutineExercise.objects.filter(day__routine=new.routine).first()
        extra.delete()                                           # el entrenador ajusta la copia
        approved = self.approve(new, self.trainer_a)
        self.plan.refresh_from_db()
        self.assertEqual((approved.status, self.plan.status), (Plan.STATUS_APPROVED, Plan.STATUS_SUPERSEDED))
        self.assertFalse(self.plan.routine.is_active)
        self.assertTrue(approved.routine.is_active)
        self.assertEqual(Plan.objects.filter(client=self.client_a, kind=KIND_EXERCISE,
                                             status=Plan.STATUS_APPROVED).count(), 1)
        self.login(self.client_a)
        shown = [routine.pk for routine in self.client.get(reverse('member_routine')).context['routines']]
        self.assertIn(approved.routine_id, shown)
        self.assertNotIn(self.plan.routine_id, shown)

    def test_states_that_can_be_cloned_and_those_that_cannot(self):
        for status in (Plan.STATUS_APPROVED, Plan.STATUS_SUPERSEDED, Plan.STATUS_ARCHIVED):
            with self.subTest(status=status):
                Plan.objects.filter(parent=self.plan).delete()
                Plan.objects.filter(pk=self.plan.pk).update(status=status)
                before = counts()
                response = self.clone(self.plan)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(counts()[0], before[0] + 1)
        Plan.objects.filter(parent=self.plan).delete()
        for status in (Plan.STATUS_DRAFT_AI, Plan.STATUS_IN_REVIEW):
            with self.subTest(status=status):
                Plan.objects.filter(pk=self.plan.pk).update(status=status)
                before = counts()
                response = self.clone(self.plan)
                self.assertEqual(messages_of(response), [texts.CLONE_MESSAGES['is_draft']])
                self.assertEqual(counts(), before)

    def test_ficha_changed_makes_the_copy_stale_and_flags_are_recomputed_today(self):
        self.assertEqual(self.plan.flags, [])
        self.make_profile(self.client_a, injuries=['knee'])        # ficha nueva con una lesión (Y04)
        self.clone(self.plan)
        new = self.newest()
        self.assertTrue(services.is_stale(new))                    # se hizo con la ficha anterior
        self.assertEqual(new.flags, ['Y04_JOINT'])                 # advertencias de HOY, no las del original
        with self.assertRaises(services.PlanStateError):
            services.approve_plan(Plan.objects.get(pk=new.pk), self.trainer_a, warnings_ack=True)
        self.assertEqual(self.approve(new, self.trainer_a).status, Plan.STATUS_APPROVED)

    def test_same_ficha_is_not_stale(self):
        self.clone(self.plan)
        self.assertFalse(services.is_stale(self.newest()))

    def test_incompatible_exercises_are_a_warning_not_a_block(self):
        # La ficha ahora exige otro equipo y el ejercicio de la copia ya no pasa el filtro
        self.make_profile(self.client_a, training_place='home', equipment=['bands'])
        response = self.clone(self.plan)
        new = self.newest()
        warnings = [m for m in messages_of(response) if 'ya no son compatibles' in m]
        self.assertEqual(len(warnings), 1)
        self.assertTrue(services.routine_conflicts(new))
        self.assertEqual(self.approve(new, self.trainer_a).status, Plan.STATUS_APPROVED)   # no bloquea

    def test_red_flag_does_not_block_an_exercise_clone(self):
        BodyMeasurement.objects.create(user=self.client_a, trainer=self.trainer_a, weight=40, height='1.65')
        self.assertEqual(self.clone(self.plan).status_code, 302)
        self.assertEqual(self.newest().parent_id, self.plan.pk)

    def test_adaptation_does_not_count_a_correction_twice(self):
        ids = sorted(RoutineExercise.objects.filter(day__routine=self.plan.routine).values_list('exercise_id', flat=True))
        Plan.objects.filter(pk=self.plan.pk).update(ai_item_ids=[*ids, 9999])   # el entrenador quitó el 9999
        self.clone(self.plan)
        new = self.approve(self.newest(), self.trainer_a)
        self.assertEqual(new.ai_item_ids, [])
        removals = adaptation._trainer_removals(self.client_a)
        self.assertEqual(len(removals), 1)                         # solo el plan original; la copia se ignora
        self.assertEqual(removals[0].exercise_ids, frozenset({9999}))

    def test_a_plan_without_a_routine_has_nothing_to_copy(self):
        Plan.objects.filter(pk=self.plan.pk).update(routine=None)
        before = counts()
        response = self.clone(self.plan)
        self.assertEqual(messages_of(response), [texts.CLONE_MESSAGES['no_content']])
        self.assertEqual(counts(), before)


class CloneNutritionTests(CloneTestCase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_nutrition(self.client_a, approve=True)
        self.lunch = self.plan.nutrition.slots.get(code='lunch')

    def test_copies_targets_slots_and_only_the_active_options(self):
        old = MealOption.objects.create(
            slot=self.lunch, position=1, name='Vieja', kcal=100, protein_g=1, carbs_g=1, fat_g=1, is_active=False)
        flagged = self.lunch.options.filter(is_active=True).first()
        MealOption.objects.filter(pk=flagged.pk).update(needs_review=True, origin=MealOption.ORIGIN_CLIENT_REGEN,
                                                         replaces=old)
        original = nutrition_rows(self.plan)
        original_targets = NutritionPlan.objects.filter(plan=self.plan).values().get()
        with self.fake_ai() as fake:
            response = self.clone(self.plan, reason='Más proteína')
            self.assertEqual(fake.calls, [])
        new = self.newest()
        self.assertEqual((new.kind, new.status, new.parent_id), (KIND_NUTRITION, Plan.STATUS_IN_REVIEW, self.plan.pk))
        copied = NutritionPlan.objects.filter(plan=new).values().get()
        for key in ('id', 'plan_id'):
            original_targets.pop(key), copied.pop(key)
        self.assertEqual(copied, original_targets)                  # objetivos idénticos (no recalcula)
        self.assertEqual(nutrition_rows(new), original)
        self.assertEqual(MealOption.objects.filter(slot__nutrition_plan__plan=new).count(),
                         MealOption.objects.filter(slot__nutrition_plan__plan=self.plan, is_active=True).count())
        self.assertFalse(MealOption.objects.filter(slot__nutrition_plan__plan=new, is_active=False).exists())
        new_options = MealOption.objects.filter(slot__nutrition_plan__plan=new)
        self.assertFalse(new_options.filter(needs_review=True).exists())
        self.assertFalse(new_options.exclude(replaces=None).exists())
        self.assertTrue(new_options.filter(origin=MealOption.ORIGIN_CLIENT_REGEN).exists())   # procedencia conservada
        self.assertEqual(new.events.get().note,
                         f'Copia de la versión {self.plan.version}. Motivo: Más proteína '
                         'Incluye 1 opción(es) pedidas por la clienta que no estaban revisadas.')
        self.assertEqual(response.status_code, 302)

    def test_nutrient_snapshot_is_copied_and_independent(self):
        source = MealOption.objects.filter(slot__nutrition_plan__plan=self.plan, is_active=True).first()
        self.assertTrue(source.nutrient_snapshot['ingredients'])
        self.clone(self.plan)
        new = self.newest()
        copy_option = MealOption.objects.get(slot__nutrition_plan__plan=new, slot__code=source.slot.code,
                                             position=source.position)
        self.assertEqual(copy_option.nutrient_snapshot, source.nutrient_snapshot)
        copy_option.nutrient_snapshot['totals']['kcal'] = '1'
        copy_option.save()
        source.refresh_from_db()
        self.assertNotEqual(source.nutrient_snapshot['totals']['kcal'], '1')

    def test_the_clients_choices_are_not_copied_and_stay_with_the_approved_plan(self):
        option = self.lunch.options.filter(is_active=True).first()
        choice = MealChoice.objects.create(client=self.client_a, slot=self.lunch, option=option,
                                           date=timezone.localdate(), completed_at=timezone.now())
        self.clone(self.plan)
        new = self.newest()
        self.assertFalse(MealChoice.objects.filter(slot__nutrition_plan__plan=new).exists())
        choice.refresh_from_db()
        self.assertIsNotNone(choice.completed_at)
        self.login(self.client_a)
        page = self.client.get(reverse('member_nutrition_plan'))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context['plan'], self.plan)           # la clienta sigue en la versión aprobada

    def test_an_option_with_a_food_that_no_longer_fits_blocks_approval_until_edited(self):
        milk = Food.objects.get(name='Leche entera')
        option = self.lunch.options.filter(is_active=True).first()
        MealOptionItem.objects.filter(option=option).update(food=milk)
        self.make_profile(self.client_a, allergies=['milk'], allergy_severity='mild')
        response = self.clone(self.plan)
        new = self.newest()
        self.assertTrue(nutrition.option_conflicts(new))
        self.assertTrue(any('no son compatibles con la clienta' in m for m in messages_of(response)))
        with self.assertRaises(services.PlanStateError) as caught:
            self.approve(new, self.boss)
        self.assertIn('no son compatibles', str(caught.exception))
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, Plan.STATUS_APPROVED)

    def test_a_red_flag_today_refuses_the_nutrition_clone_but_not_the_exercise_one(self):
        exercise = self.make_exercise(self.client_a, approve=True)
        BodyMeasurement.objects.create(user=self.client_a, trainer=self.trainer_a, weight=40, height='1.65')
        before = counts()
        response = self.clone(self.plan)
        self.assertEqual(messages_of(response), [texts.CLONE_MESSAGES['red_flag']])
        self.assertEqual(counts(), before)
        self.assertEqual(self.clone(exercise).status_code, 302)
        self.assertEqual(self.newest(kind=KIND_EXERCISE).parent_id, exercise.pk)

    def test_outside_the_wellness_scope_refuses_the_clone(self):
        self.make_profile(self.client_a, conditions=['asthma'], condition_controlled=True)
        before = counts()
        response = self.clone(self.plan)
        self.assertEqual(messages_of(response), [texts.CLONE_MESSAGES['out_of_scope']])
        self.assertEqual(counts(), before)

    def test_a_nutrition_plan_without_targets_has_nothing_to_copy(self):
        NutritionPlan.objects.filter(plan=self.plan).delete()
        before = counts()
        response = self.clone(self.plan)
        self.assertEqual(messages_of(response), [texts.CLONE_MESSAGES['no_content']])
        self.assertEqual(counts(), before)

    def test_clone_edit_approve_keeps_one_approved_nutrition_plan(self):
        self.clone(self.plan)
        new = self.newest()
        approved = self.approve(new, self.boss)
        self.plan.refresh_from_db()
        self.assertEqual((approved.status, self.plan.status), (Plan.STATUS_APPROVED, Plan.STATUS_SUPERSEDED))
        self.assertEqual(Plan.objects.filter(client=self.client_a, kind=KIND_NUTRITION,
                                             status=Plan.STATUS_APPROVED).count(), 1)
        self.login(self.client_a)
        self.assertEqual(self.client.get(reverse('member_nutrition_plan')).context['plan'], approved)


class CloneRulesTests(CloneTestCase):
    def setUp(self):
        super().setUp()
        self.plan = self.exercise_pair()

    def test_double_submit_returns_the_same_copy_without_a_second_event(self):
        first = self.clone(self.plan)
        new = self.newest()
        before = counts()
        second = self.clone(self.plan)
        self.assertRedirects(first, reverse('trainer_plan_detail', args=[new.pk]), fetch_redirect_response=False)
        self.assertRedirects(second, reverse('trainer_plan_detail', args=[new.pk]), fetch_redirect_response=False)
        self.assertEqual(counts(), before)
        self.assertEqual(PlanEvent.objects.filter(action=PlanEvent.ACTION_CLONED).count(), 1)
        self.assertEqual(messages_of(second), [texts.CLONE_MESSAGES['existing'].format(version=new.version)])

    def test_the_source_plan_row_is_locked_while_cloning(self):
        """El doble clic en paralelo espera al primero gracias al bloqueo de fila. SQLite no lo ejecuta (las
        pruebas son secuenciales), así que se comprueba que el servicio lo pide sobre el plan origen."""
        from django.db.models.query import QuerySet
        locked = []
        original = QuerySet.select_for_update

        def spy(queryset, *args, **kwargs):
            locked.append(queryset.model)
            return original(queryset, *args, **kwargs)

        with mock.patch.object(QuerySet, 'select_for_update', spy):
            cloning.clone_plan(self.plan, self.trainer_a)
        self.assertIn(Plan, locked)

    def test_service_is_idempotent_too(self):
        first, created = cloning.clone_plan(self.plan, self.trainer_a)
        again, created_again = cloning.clone_plan(self.plan, self.boss)
        self.assertEqual((created, created_again, first.pk), (True, False, again.pk))

    def test_another_draft_of_the_same_kind_refuses_the_clone(self):
        other = self.make_exercise(self.client_a)                   # un borrador de la IA del mismo tipo
        before = counts()
        response = self.clone(self.plan)
        self.assertEqual(messages_of(response), [
            f'Ya hay un borrador de ejercicio (versión {other.version}) en revisión. Ábrelo o archívalo antes '
            'de clonar otro.'])
        self.assertEqual(counts(), before)

    def test_a_draft_of_the_other_kind_does_not_get_in_the_way(self):
        self.make_nutrition(self.client_a)
        self.assertEqual(self.clone(self.plan).status_code, 302)
        self.assertEqual(self.newest(kind=KIND_EXERCISE).parent_id, self.plan.pk)

    def test_a_job_in_progress_refuses_the_clone(self):
        PlanGenerationJob.objects.create(
            kind=KIND_EXERCISE, client=self.client_a, requested_by=self.trainer_a, requested_by_role='trainer',
            status=PlanGenerationJob.STATUS_RUNNING, request_token=uuid.uuid4())
        before = counts()
        response = self.clone(self.plan)
        self.assertEqual(messages_of(response), [texts.CLONE_MESSAGES['job_running']])
        self.assertEqual(counts(), before)

    def test_a_simulated_plan_cannot_be_cloned_outside_debug(self):
        Plan.objects.filter(pk=self.plan.pk).update(is_simulated=True)
        before = counts()
        response = self.clone(self.plan)
        self.assertEqual(messages_of(response), [texts.CLONE_MESSAGES['simulated']])
        self.assertEqual(counts(), before)
        with override_settings(DEBUG=True):            # en desarrollo sí (el servicio no pasa por el middleware)
            new, created = cloning.clone_plan(self.plan, self.trainer_a)
        self.assertTrue(created and new.is_simulated)

    def test_a_lost_race_for_the_version_number_leaves_nothing_behind(self):
        before = counts()
        with mock.patch('apps.plans.services.next_version', return_value=self.plan.version), \
                self.assertLogs('apps.plans.cloning', level='WARNING') as logs:
            response = self.clone(self.plan)
        self.assertEqual(logs.output, [
            f'WARNING:apps.plans.cloning:plan clone conflict source_id={self.plan.pk} actor_id={self.trainer_a.pk}'])
        self.assertEqual(messages_of(response), [texts.CLONE_MESSAGES['race']])
        self.assertEqual(counts(), before)                          # ni la rutina copiada quedó

    def test_reason_validation(self):
        before = counts()
        for label, reason, expected in (
                ('con HTML', '<b>x</b>', 'No uses los signos'),
                ('muy largo', 'x' * 301, 'máximo 300')):
            with self.subTest(label):
                response = self.clone(self.plan, reason=reason)
                self.assertEqual(response.status_code, 400)
                self.assertIn(expected, response.context['clone_errors']['reason'])
        self.assertEqual(counts(), before)
        self.assertEqual(self.clone(self.plan, reason='x' * 300).status_code, 302)

    def test_logs_only_carry_ids(self):
        with self.assertLogs('apps.plans.cloning', level='INFO') as logs:
            self.clone(self.plan, reason='Algo privado que escribió el entrenador')
        new = self.newest()
        self.assertEqual(logs.output, [
            f'INFO:apps.plans.cloning:plan cloned plan_id={new.pk} source_id={self.plan.pk} '
            f'actor_id={self.trainer_a.pk}'])


class CloneStateContextTests(CloneTestCase):
    """El diccionario `clone` que recibe `trainer/plan_detail.html` (sección 7.8)."""

    def setUp(self):
        super().setUp()
        self.plan = self.exercise_pair()

    def test_a_pending_plan_has_no_clone_block(self):
        draft = self.make_exercise(self.client_a)
        self.assertIsNone(self.detail_context(draft)['clone'])
        self.assertEqual(self.detail_context(draft)['clone_errors'], {})

    def test_an_approved_plan_can_be_cloned_and_says_which_version_it_replaces(self):
        clone = self.detail_context(self.plan)['clone']
        self.assertEqual(clone, {'allowed': True, 'blocked_reason': '', 'existing_draft': None,
                                 'other_draft': None, 'replaces_version': self.plan.version,
                                 'reason_max': 300})

    def test_after_cloning_the_original_points_to_its_copy(self):
        self.clone(self.plan)
        new = self.newest()
        clone = self.detail_context(self.plan)['clone']
        self.assertFalse(clone['allowed'])
        self.assertEqual((clone['existing_draft'], clone['blocked_reason']),
                         (new, texts.CLONE_MESSAGES['has_copy']))
        self.assertIsNone(clone['other_draft'])

    def test_other_draft_and_inactive_account_and_old_versions(self):
        old = Plan.objects.get(pk=self.plan.pk)
        Plan.objects.filter(pk=old.pk).update(status=Plan.STATUS_SUPERSEDED)
        current = Plan.objects.create(client=self.client_a, kind=KIND_EXERCISE, version=old.version + 1,
                                      status=Plan.STATUS_APPROVED)
        clone = self.detail_context(old)['clone']
        self.assertTrue(clone['allowed'])
        self.assertEqual(clone['replaces_version'], current.version)   # volver a una versión anterior
        other = self.make_exercise(self.client_a)
        clone = self.detail_context(old)['clone']
        self.assertEqual((clone['allowed'], clone['other_draft']), (False, other))
        User.objects.filter(pk=self.client_a.pk).update(is_active=False, deactivated_at=timezone.now())
        self.assertEqual(self.detail_context(old)['clone']['blocked_reason'], INACTIVE_CLIENT_MESSAGE)
