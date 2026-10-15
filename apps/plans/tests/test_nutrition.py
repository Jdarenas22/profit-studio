"""Plan de alimentación (fase C) sin red: pedido -> trabajo (una llamada por comida) -> borrador con
3 opciones por comida -> revisión -> aprobación con credencial -> "otra opción".

Cubre (contrato, sección 11): bandera roja y falta de alimentos sin llamar a la IA, restricciones
de alergias/dieta/advertencias en lo que se envía y en lo que se guarda, validación con un
reintento, que la salida no validada no se guarde, privacidad del payload, cupos de "otra
opción", y que la clienta no vea nada hasta que se apruebe.
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.test import override_settings
from django.utils import timezone

from apps.health import services as health
from apps.plans import nutrition, services
from apps.plans.models import (
    Food, MealChoice, MealOption, MealOptionItem, Plan, PlanEvent, PlanGenerationJob,
)
from apps.plans.rules import foods as rules_foods
from apps.plans.rules import meal_validate as rules_meal

from .helpers import NutritionScenarioTestCase

HYPERTENSION = dict(conditions=['hypertension'], condition_controlled='on', medical_clearance='on')


def hypertension_profile():
    clearance = (timezone.localdate() - timedelta(days=30)).isoformat()
    return dict(HYPERTENSION, clearance_date=clearance)


def plan_foods(plan):
    return set(MealOptionItem.objects.filter(
        option__slot__nutrition_plan__plan=plan).values_list('food__name', flat=True))


class GenerationTests(NutritionScenarioTestCase):
    def test_success_creates_a_draft_with_three_equivalent_options_per_meal(self):
        self.ready(self.client_a)
        job, fake = self.request_nutrition(self.client_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        plan = job.plan
        self.assertEqual((plan.kind, plan.status, plan.version), ('nutrition', Plan.STATUS_DRAFT_AI, 1))
        self.assertTrue(plan.is_simulated)
        self.assertIsNone(plan.routine)
        self.assertEqual(plan.profile_version, plan.health_profile.version)
        self.assertEqual(len(fake.calls), 3)                                    # una llamada por comida
        self.assertEqual((job.progress_done, job.progress_total), (3, 3))
        self.assertEqual((job.tokens_in, job.tokens_out), (3000, 1500))
        goal = plan.nutrition
        self.assertEqual(goal.meals_per_day, 3)
        self.assertEqual(goal.formula, 'mifflin_st_jeor/1')
        slots = list(goal.slots.all())
        self.assertEqual([s.code for s in slots], ['breakfast', 'lunch', 'dinner'])
        self.assertEqual([s.time_hint for s in slots], ['07:00', '12:30', '19:00'])
        for slot in slots:
            options = list(slot.options.filter(is_active=True))
            self.assertEqual([o.position for o in options], [1, 2, 3])
            for option in options:
                self.assertTrue(option.items.count() >= 2)
                self.assertEqual(option.origin, MealOption.ORIGIN_AI)
                self.assertFalse(option.needs_review)
                self.assertFalse(option.out_of_band)
                self.assertLessEqual(abs(option.kcal - slot.kcal_target), slot.kcal_target * Decimal('0.05'))
        self.assertTrue(PlanEvent.objects.filter(plan=plan, action=PlanEvent.ACTION_CREATED).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn(self.client_a.first_name, mail.outbox[0].body)

    def test_every_combination_of_options_stays_inside_the_daily_band(self):
        plan = self.nutrition_draft(self.client_a)
        goal = plan.nutrition
        per_slot = [[rules_foods.Totals(o.kcal, o.protein_g, o.carbs_g, o.fat_g) for o in slot.options.all()]
                    for slot in goal.slots.all()]
        self.assertEqual(rules_meal.check_day(per_slot, goal.kcal_target, goal.protein_g, 1200), [])
        low = sum(min(t.kcal for t in options) for options in per_slot)
        high = sum(max(t.kcal for t in options) for options in per_slot)
        self.assertGreaterEqual(low, goal.kcal_target * Decimal('0.94'))
        self.assertLessEqual(high, goal.kcal_target * Decimal('1.06'))

    def test_targets_are_calculated_by_python(self):
        plan = self.nutrition_draft(self.client_a)           # 30 años (aprox.), F, 60 kg, 1,65 m, ligera, tonificar
        goal = plan.nutrition
        # IMC 22: tonificar = mantenimiento; el gasto sale de Mifflin-St Jeor
        self.assertEqual(goal.adjustment_pct, 0)
        self.assertEqual(goal.kcal_target, int(goal.get))
        self.assertAlmostEqual(float(goal.activity_factor), 1.375)
        self.assertGreater(goal.protein_g, 60)
        self.assertEqual(goal.warnings, [])

    def test_payload_is_minimal_and_has_no_restrictions_in_it(self):
        self.ready(self.client_a, allergies=['peanut'], allergy_severity='anaphylaxis', diet_type='vegetarian',
                   other_condition_text='Algo privado', disliked_foods_text='Higado')
        job, fake = self.request_nutrition(self.client_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        payload = fake.calls[0]['payload']
        sent = json.dumps(payload, ensure_ascii=False)
        for secret in (self.client_a.username, self.client_a.email, self.client_a.first_name,
                       self.client_a.last_name, self.trainer_a.username, 'Algo privado', 'Higado',
                       'peanut', 'anaphylaxis', 'Maní', 'birth_date'):
            self.assertNotIn(secret, sent)
        self.assertEqual(set(payload), {'task', 'schema_version', 'job_ref', 'slot', 'options_requested', 'client',
                                        'allowed_foods', 'existing_options', 'avoid_repeating_names',
                                        'trainer_notes', 'previous_errors'})
        self.assertEqual(set(payload['client']), {'age', 'sex', 'goal', 'diet', 'cooking', 'budget', 'conservative'})
        self.assertTrue(payload['client']['conservative'])                    # alergia grave = advertencia Y07
        self.assertEqual(payload['client']['diet'], 'vegetarian')
        names = {row['name'] for row in payload['allowed_foods']}
        self.assertFalse(names & {'Maní tostado sin sal', 'Almendras', 'Pechuga de pollo cocida sin piel'})
        self.assertEqual(payload['slot']['code'], 'breakfast')
        self.assertEqual(fake.calls[1]['payload']['slot']['code'], 'lunch')
        self.assertTrue(set(fake.calls[1]['payload']['avoid_repeating_names']))   # no repite platos entre comidas

    def test_restrictions_reach_the_saved_plan(self):
        plan = self.nutrition_draft(self.client_a, allergies=['milk', 'gluten'], allergy_severity='mild',
                                    diet_type='pescatarian', intolerances=['fructose'])
        used = plan_foods(plan)
        self.assertTrue(used)
        self.assertFalse(used & {'Leche entera', 'Queso fresco campesino', 'Pasta cocida', 'Pan integral tajado',
                                 'Pechuga de pollo cocida sin piel', 'Carne de res magra cocida',
                                 'Mango', 'Manzana', 'Avena en hojuelas (cruda)', 'Chocolate oscuro 70 %'})

    def test_hypertension_removes_high_sodium_foods(self):
        plan = self.nutrition_draft(self.client_a, **hypertension_profile())
        self.assertIn('Y01_HYPERTENSION', plan.flags)
        self.assertFalse(plan_foods(plan) & {'Queso fresco campesino', 'Galletas de soda', 'Atún en agua (escurrido)'})

    def test_bad_ai_output_retries_once_with_codes_and_saves_nothing(self):
        self.ready(self.client_a, allergies=['peanut'], allergy_severity='mild')
        for scenario, code in (('allergen_text', 'validation_failed:V03_ALLERGEN'),
                               ('unknown_id', 'validation_failed:V02_IDS'),
                               ('wrong_count', 'validation_failed:V01_SCHEMA'),
                               ('kcal_out_of_band', 'validation_failed:V04_KCAL'),
                               ('forbidden_text', 'validation_failed:V08_TEXT'),
                               ('invalid_json', 'validation_failed:V01_SCHEMA'),
                               ('max_tokens', 'ai_finish_max_tokens')):
            with self.subTest(scenario=scenario):
                PlanGenerationJob.objects.all().delete()
                job, fake = self.request_nutrition(self.client_a, scenario=scenario)
                self.assertEqual((job.status, job.error_code), (PlanGenerationJob.STATUS_FAILED, code))
                self.assertEqual(len(fake.calls), 2)                            # primera respuesta + 1 reintento
                self.assertEqual(fake.calls[1]['payload']['previous_errors'], [code.split(':')[-1]]
                                 if ':' in code else ['V01_SCHEMA'])
                self.assertFalse(Plan.objects.exists())
                self.assertFalse(MealOption.objects.exists())

    def test_one_bad_meal_is_retried_without_redoing_the_others(self):
        self.ready(self.client_a)
        job, fake = self.request_nutrition(self.client_a, scenario=['ok', 'unknown_id', 'ok', 'ok'])
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        self.assertEqual([c['payload']['slot']['code'] for c in fake.calls], ['breakfast', 'lunch', 'lunch', 'dinner'])
        self.assertEqual(fake.calls[2]['payload']['previous_errors'], ['V02_IDS'])

    def test_transient_errors_are_retried(self):
        self.ready(self.client_a)
        job, fake = self.request_nutrition(self.client_a, scenario=['rate_limited', 'ok'])
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED)
        self.assertEqual(len(fake.calls), 4)

    def test_red_flag_blocks_nutrition_without_calling_the_ai_but_not_exercise(self):
        self.ready(self.client_a, eating_disorder_history='yes')
        job, fake = self.request_nutrition(self.client_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_BLOCKED)
        self.assertIn('R03_EATING_DISORDER', job.flags)
        self.assertEqual(fake.calls, [])
        self.assertFalse(Plan.objects.filter(kind='nutrition').exists())
        self.assertNotIn('trastorno', mail.outbox[-1].body.lower())
        exercise, _ = self.request(self.client_a)
        self.assertEqual(exercise.status, PlanGenerationJob.STATUS_SUCCEEDED)
        self.assertIn('Y10_ED_EXERCISE', exercise.flags)

    def test_not_enough_foods_blocks_without_calling_the_ai(self):
        self.ready(self.client_a)
        Food.objects.exclude(pk__in=Food.objects.values_list('pk', flat=True)[:15]).update(is_active=False)
        job, fake = self.request_nutrition(self.client_a)
        self.assertEqual((job.status, job.flags), (PlanGenerationJob.STATUS_BLOCKED, ['D06_FEW_FOODS']))
        self.assertEqual(fake.calls, [])
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(AI_PLANS_REQUIRE_PROFESSIONAL=True)
    def test_unvalidated_foods_are_ignored_outside_debug(self):
        self.ready(self.client_a)
        Food.objects.update(validated_by='')
        job, fake = self.request_nutrition(self.client_a)
        self.assertEqual((job.status, job.flags), (PlanGenerationJob.STATUS_BLOCKED, ['D06_FEW_FOODS']))
        with override_settings(DEBUG=True):
            PlanGenerationJob.objects.all().delete()
            job, _ = self.request_nutrition(self.client_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)

    @override_settings(AI_PLANS_REQUIRE_PROFESSIONAL=False)
    def test_suspended_validation_uses_unvalidated_foods(self):
        self.ready(self.client_a)
        Food.objects.update(validated_by='')
        job, _fake = self.request_nutrition(self.client_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)

    def test_missing_requirements_create_nothing(self):
        self.grant(self.client_a)
        with self.assertRaises(services.PlanRequestNotReady) as ctx:
            self.request_nutrition(self.client_a)
        self.assertIn('D02_PROFILE', ctx.exception.eligibility.missing)
        self.assertFalse(PlanGenerationJob.objects.exists())

    def test_a_trainer_can_request_for_own_client_only(self):
        self.ready(self.client_a)
        job, _ = self.request_nutrition(self.client_a, actor=self.trainer_a)
        self.assertEqual((job.requested_by, job.requested_by_role), (self.trainer_a, 'trainer'))
        with self.assertRaises(services.PlanNotAllowed):
            self.request_nutrition(self.client_a, actor=self.trainer_b)

    def test_regenerate_archives_the_parent_and_keeps_versions(self):
        plan = self.nutrition_draft(self.client_a)
        with self.fake_ai() as fake, self.captureOnCommitCallbacks(execute=True):
            job, _ = services.regenerate_plan(plan, self.trainer_a, 'Más económico', services.new_request_token())
        job.refresh_from_db()
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED)
        self.assertEqual(fake.calls[0]['payload']['trainer_notes'], 'Más económico')
        plan.refresh_from_db()
        self.assertEqual(plan.status, Plan.STATUS_ARCHIVED)
        self.assertEqual((job.plan.version, job.plan.parent), (2, plan))

    def test_nutrition_and_exercise_jobs_are_independent(self):
        self.ready(self.client_a)
        self.request(self.client_a)
        job, _ = self.request_nutrition(self.client_a, actor=self.trainer_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED)
        self.assertEqual(Plan.objects.filter(client=self.client_a).count(), 2)

    def test_logs_have_no_health_data(self):
        self.ready(self.client_a, other_condition_text='Algo privado', allergies=['peanut'], allergy_severity='mild')
        with self.assertLogs('apps.plans', level='INFO') as logs:
            self.request_nutrition(self.client_a)
        text = '\n'.join(logs.output)
        self.assertIn('status=succeeded', text)
        for secret in ('Algo privado', 'peanut', 'Maní', self.client_a.username, self.client_a.email):
            self.assertNotIn(secret, text)


class ApprovalTests(NutritionScenarioTestCase):
    @override_settings(AI_PLANS_REQUIRE_PROFESSIONAL=True)
    def test_only_someone_with_the_credential_can_approve(self):
        plan = self.nutrition_draft(self.client_a)
        Plan.objects.filter(pk=plan.pk).update(is_simulated=False)
        plan.refresh_from_db()
        with self.assertRaises(services.PlanNotAllowed):
            services.approve_plan(plan, self.trainer_a)                       # asignado, sin credencial
        self.assertFalse(services.can_validate_nutrition(self.trainer_a))
        approved = services.approve_plan(plan, self.boss, note_for_client='Buen provecho')
        self.assertEqual(approved.status, Plan.STATUS_APPROVED)
        self.assertEqual(approved.reviewer_credential, self.CREDENTIAL)
        self.assertEqual(approved.approved_by, self.boss)
        self.assertEqual(approved.valid_until, health.dates.today() + timedelta(days=28))
        self.assertEqual(services.approved_plan(self.client_a, 'nutrition'), approved)

    @override_settings(AI_PLANS_REQUIRE_PROFESSIONAL=False)
    def test_suspended_validation_lets_the_assigned_trainer_approve(self):
        plan = self.nutrition_draft(self.client_a)
        Plan.objects.filter(pk=plan.pk).update(is_simulated=False)
        plan.refresh_from_db()
        self.assertTrue(services.can_validate_nutrition(self.trainer_a))
        approved = services.approve_plan(plan, self.trainer_a)
        self.assertEqual(approved.status, Plan.STATUS_APPROVED)
        self.assertEqual(approved.reviewer_credential, '')

    def test_a_trainer_with_the_credential_can_approve_their_clients(self):
        from apps.plans.models import TrainerCredential
        TrainerCredential.objects.create(user=self.trainer_a, can_validate_nutrition=True,
                                         registration_label='Entrenador con registro')
        plan = self.nutrition_draft(self.client_a)
        Plan.objects.filter(pk=plan.pk).update(is_simulated=False)
        plan.refresh_from_db()
        self.assertEqual(services.approve_plan(plan, self.trainer_a).reviewer_credential, 'Entrenador con registro')
        with self.assertRaises(services.PlanNotAllowed):                       # y no las de otro entrenador
            services.approve_plan(Plan.objects.get(pk=plan.pk), self.trainer_b)

    def test_simulated_plans_are_not_approved_in_production(self):
        plan = self.nutrition_draft(self.client_a)
        with self.assertRaises(services.PlanStateError):
            services.approve_plan(plan, self.boss)
        self.assertEqual(Plan.objects.get(pk=plan.pk).status, Plan.STATUS_DRAFT_AI)

    def test_warnings_must_be_acknowledged(self):
        plan = self.nutrition_draft(self.client_a, **hypertension_profile())
        Plan.objects.filter(pk=plan.pk).update(is_simulated=False)
        plan.refresh_from_db()
        with self.assertRaises(services.PlanStateError):
            services.approve_plan(plan, self.boss)
        self.assertEqual(services.approve_plan(plan, self.boss, warnings_ack=True).status, Plan.STATUS_APPROVED)

    def test_new_approval_supersedes_the_previous_one(self):
        first = self.approved_nutrition(self.client_a)
        job, _ = self.request_nutrition(self.client_a)
        second = job.plan
        Plan.objects.filter(pk=second.pk).update(is_simulated=False)
        second.refresh_from_db()
        services.approve_plan(second, self.boss)
        first.refresh_from_db()
        self.assertEqual(first.status, Plan.STATUS_SUPERSEDED)
        self.assertEqual(Plan.objects.filter(client=self.client_a, kind='nutrition', status='approved').count(), 1)

    def test_approval_is_blocked_when_a_meal_lacks_three_options(self):
        plan = self.nutrition_draft(self.client_a)
        Plan.objects.filter(pk=plan.pk).update(is_simulated=False)
        plan.refresh_from_db()
        MealOption.objects.filter(slot__nutrition_plan__plan=plan, position=3, slot__code='lunch').update(is_active=False)
        with self.assertRaises(services.PlanStateError) as ctx:
            services.approve_plan(plan, self.boss)
        self.assertIn('exactamente 3 opciones', str(ctx.exception))

    def test_approval_is_blocked_by_an_option_that_conflicts_with_the_allergies(self):
        plan = self.nutrition_draft(self.client_a)
        Plan.objects.filter(pk=plan.pk).update(is_simulated=False)
        milk = Food.objects.get(name='Leche entera')
        item = MealOptionItem.objects.filter(option__slot__nutrition_plan__plan=plan).first()
        MealOptionItem.objects.filter(pk=item.pk).update(food=milk)
        self.make_profile(self.client_a, allergies=['milk'], allergy_severity='mild')    # ficha nueva
        plan.refresh_from_db()
        self.assertTrue(nutrition.option_conflicts(plan))
        with self.assertRaises(services.PlanStateError) as ctx:
            services.approve_plan(plan, self.boss, stale_ack=True)
        self.assertIn('no son compatibles', str(ctx.exception))

    def test_approval_is_blocked_when_the_current_profile_has_a_red_flag(self):
        plan = self.nutrition_draft(self.client_a)
        Plan.objects.filter(pk=plan.pk).update(is_simulated=False)
        self.make_profile(self.client_a, pregnancy_status='pregnant')
        plan.refresh_from_db()
        with self.assertRaises(services.PlanStateError) as ctx:
            services.approve_plan(plan, self.boss, stale_ack=True)
        self.assertIn('valoración profesional', str(ctx.exception))
        self.assertNotIn('embarazo', str(ctx.exception).lower())

    def test_a_stale_plan_needs_confirmation(self):
        plan = self.nutrition_draft(self.client_a)
        Plan.objects.filter(pk=plan.pk).update(is_simulated=False)
        self.make_profile(self.client_a)
        plan.refresh_from_db()
        with self.assertRaises(services.PlanStateError):
            services.approve_plan(plan, self.boss)
        self.assertEqual(services.approve_plan(plan, self.boss, stale_ack=True).status, Plan.STATUS_APPROVED)

    def test_the_client_sees_nothing_until_approval(self):
        plan = self.nutrition_draft(self.client_a)
        self.assertIsNone(nutrition.member_plan(self.client_a))
        Plan.objects.filter(pk=plan.pk).update(is_simulated=False)
        plan.refresh_from_db()
        services.approve_plan(plan, self.boss)
        self.assertEqual(nutrition.member_plan(self.client_a), plan)
        self.assertIsNone(nutrition.member_plan(self.client_b))

    def test_archiving_an_approved_plan_hides_it(self):
        plan = self.approved_nutrition(self.client_a)
        services.archive_plan(plan, self.trainer_a, 'Cambió de objetivo')
        self.assertIsNone(nutrition.member_plan(self.client_a))


class TrainerEditTests(NutritionScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.plan = self.nutrition_draft(self.client_a)
        self.option = MealOption.objects.filter(slot__nutrition_plan__plan=self.plan, slot__code='lunch').first()
        self.food = {f.name: f for f in Food.objects.all()}

    def items(self, *rows):
        return [{'food_id': self.food[name].id, 'grams': grams} for name, grams in rows]

    def good_items(self):
        return self.items(('Pechuga de pollo cocida sin piel', 200), ('Arroz blanco cocido', 200), ('Tomate', 120),
                          ('Aceite de oliva', 10))

    def test_valid_edit_recalculates_and_takes_the_plan_for_review(self):
        option, result = nutrition.edit_option(self.plan, self.option, self.trainer_a, 'Pollo con arroz',
                                               'Cocina el pollo y sirve con arroz.', self.good_items())
        option.refresh_from_db()
        self.assertEqual((option.name, option.origin, option.needs_review), ('Pollo con arroz', 'trainer', False))
        expected = rules_foods.items_totals([
            (self.food['Pechuga de pollo cocida sin piel'], 200), (self.food['Arroz blanco cocido'], 200),
            (self.food['Tomate'], 120), (self.food['Aceite de oliva'], 10)])
        self.assertEqual(option.kcal, expected.kcal.quantize(Decimal('0.1')))
        self.assertEqual(option.items.count(), 4)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, Plan.STATUS_IN_REVIEW)
        self.assertTrue(PlanEvent.objects.filter(plan=self.plan, action=PlanEvent.ACTION_EDITED).exists())

    def test_energy_out_of_band_only_warns(self):
        option, result = nutrition.edit_option(self.plan, self.option, self.trainer_a, 'Ensalada', 'Mezcla.',
                                               self.items(('Lechuga', 100), ('Tomate', 100)))
        option.refresh_from_db()
        self.assertTrue(result.out_of_band)
        self.assertTrue(option.out_of_band)

    def test_invalid_edits_are_rejected_and_nothing_changes(self):
        before = list(self.option.items.values_list('food_id', 'grams'))
        cases = [
            (self.items(('Pechuga de pollo cocida sin piel', 100)), 'Pollo', 'Cocina.'),                  # 1 alimento
            ([{'food_id': 999999, 'grams': 100}] + self.items(('Tomate', 100)), 'Pollo', 'Cocina.'),      # id inventado
            (self.items(('Tomate', 100), ('Lechuga', 4)), 'Pollo', 'Cocina.'),                            # gramos
            (self.items(('Tomate', 100), ('Lechuga', 100)), '<b>Pollo</b>', 'Cocina.'),                   # HTML
            (self.items(('Tomate', 100), ('Lechuga', 100)), 'Pollo', 'Toma un suplemento.'),
        ]
        for items, name, preparation in cases:
            with self.subTest(name=name, preparation=preparation), self.assertRaises(nutrition.OptionInvalid):
                nutrition.edit_option(self.plan, self.option, self.trainer_a, name, preparation, items)
        self.assertEqual(list(self.option.items.values_list('food_id', 'grams')), before)

    def test_foods_that_conflict_with_the_allergies_are_not_selectable(self):
        self.make_profile(self.client_a, allergies=['peanut'], allergy_severity='mild')
        with self.assertRaises(nutrition.OptionInvalid) as ctx:
            nutrition.edit_option(self.plan, self.option, self.trainer_a, 'Con maní', 'Cocina.',
                                  self.items(('Maní tostado sin sal', 30), ('Arroz blanco cocido', 100)))
        self.assertIn('V02_IDS', {code for code, _ in ctx.exception.errors})
        with self.assertRaises(nutrition.OptionInvalid) as ctx:
            nutrition.edit_option(self.plan, self.option, self.trainer_a, 'Salsa de maní', 'Cocina.',
                                  self.items(('Arroz blanco cocido', 100), ('Tomate', 100)))
        self.assertIn('V03_ALLERGEN', {code for code, _ in ctx.exception.errors})

    def test_other_trainers_and_approved_plans_cannot_be_edited(self):
        with self.assertRaises(services.PlanNotAllowed):
            nutrition.edit_option(self.plan, self.option, self.trainer_b, 'x', 'y', self.good_items())
        Plan.objects.filter(pk=self.plan.pk).update(is_simulated=False)
        self.plan.refresh_from_db()
        services.approve_plan(self.plan, self.boss)
        self.plan.refresh_from_db()
        with self.assertRaises(services.PlanStateError):
            nutrition.edit_option(self.plan, self.option, self.trainer_a, 'x', 'y', self.good_items())


class AlternativeTests(NutritionScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.plan = self.approved_nutrition(self.client_a)
        self.slot = self.plan.nutrition.slots.get(code='lunch')
        self.option = self.slot.options.get(position=2)
        mail.outbox.clear()

    def alternative(self, option=None, scenario='ok', client=None):
        with self.fake_ai(scenario) as fake, self.captureOnCommitCallbacks(execute=True):
            job, created = nutrition.request_alternative(
                client or self.client_a, self.slot.pk, (option or self.option).pk, services.new_request_token())
        job.refresh_from_db()
        return job, fake

    def test_replaces_only_the_chosen_option_and_flags_it_for_review(self):
        names_before = list(self.slot.options.filter(is_active=True).values_list('name', flat=True))
        job, fake = self.alternative()
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        self.assertEqual(job.kind, 'meal_alternative')
        active = list(self.slot.options.filter(is_active=True).order_by('position'))
        self.assertEqual([o.position for o in active], [1, 2, 3])                  # siempre 3 activas
        new = active[1]
        self.option.refresh_from_db()
        self.assertFalse(self.option.is_active)
        self.assertEqual((new.origin, new.needs_review, new.replaces), ('client_regen', True, self.option))
        self.assertNotIn(new.name, names_before)
        self.assertLessEqual(abs(new.kcal - self.slot.kcal_target), self.slot.kcal_target * Decimal('0.05'))
        self.assertEqual(active[0].pk, self.slot.options.get(position=1, is_active=True).pk)
        self.assertTrue(PlanEvent.objects.filter(plan=self.plan, action=PlanEvent.ACTION_CLIENT_ALTERNATIVE).exists())
        self.assertEqual(len(mail.outbox), 1)                                      # aviso al entrenador, sin contenido
        self.assertNotIn(new.name, mail.outbox[0].body)
        self.assertEqual(self.plan.__class__.objects.get(pk=self.plan.pk).status, Plan.STATUS_APPROVED)

    def test_the_ai_gets_one_meal_and_the_existing_options_and_no_health_data(self):
        health_plan = self.make_profile(self.client_a, allergies=['peanut'], allergy_severity='mild')
        self.assertTrue(health_plan.is_current)
        job, fake = self.alternative()
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        payload = fake.calls[0]['payload']
        self.assertEqual((payload['options_requested'], payload['slot']['code']), (1, 'lunch'))
        self.assertEqual(len(payload['existing_options']), 3)
        self.assertIn(self.option.name, payload['avoid_repeating_names'])
        fresh = {key: value for key, value in payload.items() if key not in ('existing_options', 'avoid_repeating_names')}
        sent = json.dumps(fresh, ensure_ascii=False)
        for secret in ('peanut', 'Maní', self.client_a.email, self.client_a.username):
            self.assertNotIn(secret, sent)          # (el plan viejo puede nombrar maní: es contenido, no ficha)

    def test_failed_validation_keeps_the_old_option(self):
        job, fake = self.alternative(scenario='unknown_id')
        self.assertEqual(job.status, PlanGenerationJob.STATUS_FAILED)
        self.assertEqual(len(fake.calls), 2)
        self.option.refresh_from_db()
        self.assertTrue(self.option.is_active)
        self.assertEqual(self.slot.options.filter(is_active=True).count(), 3)
        self.assertFalse(MealOption.objects.filter(origin='client_regen').exists())

    def test_cannot_touch_options_of_other_plans_or_inactive_ones(self):
        other = self.approved_nutrition(self.client_b)
        foreign_slot = other.nutrition.slots.first()
        foreign_option = foreign_slot.options.first()
        token = services.new_request_token()
        for slot_pk, option_pk in ((foreign_slot.pk, foreign_option.pk), (self.slot.pk, foreign_option.pk),
                                   (foreign_slot.pk, self.option.pk)):
            with self.subTest(slot=slot_pk, option=option_pk), self.assertRaises(nutrition.OptionNotFound):
                nutrition.request_alternative(self.client_a, slot_pk, option_pk, token)
        MealOption.objects.filter(pk=self.option.pk).update(is_active=False)
        with self.assertRaises(nutrition.OptionNotFound):
            nutrition.request_alternative(self.client_a, self.slot.pk, self.option.pk, token)
        with self.assertRaises(services.PlanNotAllowed):
            nutrition.request_alternative(self.trainer_a, self.slot.pk, self.option.pk, token)
        self.assertFalse(PlanGenerationJob.objects.filter(kind='meal_alternative').exists())

    @override_settings(AI_PLANS_ALT_SLOT_WEEKLY_LIMIT=2)
    def test_weekly_limit_per_meal(self):
        for position in (1, 2):
            job, _ = self.alternative(option=self.slot.options.get(position=position, is_active=True))
            self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED)
        self.assertEqual(nutrition.alternatives_left(self.client_a, self.slot)[0], 0)
        with self.assertRaises(services.QuotaExceeded):
            self.alternative(option=self.slot.options.get(position=3, is_active=True))
        # Otra comida tiene su propio cupo
        breakfast = self.plan.nutrition.slots.get(code='breakfast')
        with self.fake_ai(), self.captureOnCommitCallbacks(execute=True):
            job, _ = nutrition.request_alternative(self.client_a, breakfast.pk, breakfast.options.first().pk,
                                                   services.new_request_token())
        job.refresh_from_db()
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED)

    @override_settings(AI_PLANS_ALT_DAILY_LIMIT=1)
    def test_daily_limit_per_client(self):
        self.alternative()
        breakfast = self.plan.nutrition.slots.get(code='breakfast')
        with self.assertRaises(services.QuotaExceeded):
            nutrition.request_alternative(self.client_a, breakfast.pk, breakfast.options.first().pk,
                                          services.new_request_token())

    def test_same_token_does_not_create_two_jobs(self):
        token = services.new_request_token()
        with self.fake_ai(), self.captureOnCommitCallbacks(execute=True):
            first, created = nutrition.request_alternative(self.client_a, self.slot.pk, self.option.pk, token)
            again, created_again = nutrition.request_alternative(self.client_a, self.slot.pk, self.option.pk, token)
        self.assertEqual((first.pk, created, created_again), (again.pk, True, False))
        self.assertEqual(PlanGenerationJob.objects.filter(kind='meal_alternative').count(), 1)

    def test_blocked_by_a_new_red_flag_without_calling_the_ai(self):
        self.make_profile(self.client_a, pregnancy_status='pregnant')
        job, fake = self.alternative()
        self.assertEqual(job.status, PlanGenerationJob.STATUS_BLOCKED)
        self.assertEqual(fake.calls, [])
        self.option.refresh_from_db()
        self.assertTrue(self.option.is_active)

    def ask_blocked(self, client=None, slot=None, option=None):
        """Pide "otra opción" con un token NUEVO (como un reenvío del formulario). Ejecuta los
        `on_commit` (el aviso por correo). Devuelve `(job, created, fake)`."""
        slot, option = slot or self.slot, option or self.option
        with self.fake_ai() as fake, self.captureOnCommitCallbacks(execute=True):
            job, created = nutrition.request_alternative(
                client or self.client_a, slot.pk, option.pk, services.new_request_token())
        return job, created, fake

    def blocked_alternatives(self, client=None):
        return PlanGenerationJob.objects.filter(
            kind='meal_alternative', client=client or self.client_a, status=PlanGenerationJob.STATUS_BLOCKED)

    def test_repeated_blocked_request_creates_one_job_and_one_notice_a_day(self):
        self.make_profile(self.client_a, pregnancy_status='pregnant')
        first, created, fake = self.ask_blocked()
        again, created_again, _ = self.ask_blocked()          # otro token, misma bandera roja
        self.assertEqual((self.blocked_alternatives().count(), len(mail.outbox)), (1, 1))
        self.assertEqual((first.pk, created, created_again), (again.pk, True, False))
        self.assertEqual((first.status, first.flags), (PlanGenerationJob.STATUS_BLOCKED, ['R02_PREGNANCY']))
        self.assertEqual(fake.calls, [])
        self.assertEqual(PlanGenerationJob.objects.filter(kind='meal_alternative').count(), 1)   # ni otros estados

    def test_blocked_request_with_other_red_flags_creates_a_new_job_and_notice(self):
        self.make_profile(self.client_a, pregnancy_status='pregnant')
        first, _, _ = self.ask_blocked()
        self.make_profile(self.client_a, eating_disorder_history='yes')          # otra bandera roja
        second, created, _ = self.ask_blocked()
        self.assertTrue(created)
        self.assertNotEqual(first.pk, second.pk)
        self.assertNotEqual(first.flags, second.flags)
        self.assertEqual(second.flags, ['R03_EATING_DISORDER'])
        self.assertEqual((self.blocked_alternatives().count(), len(mail.outbox)), (2, 2))

    def test_blocked_request_after_a_day_creates_a_new_job_and_notice(self):
        self.make_profile(self.client_a, pregnancy_status='pregnant')
        first, _, _ = self.ask_blocked()
        PlanGenerationJob.objects.filter(pk=first.pk).update(created_at=timezone.now() - timedelta(days=2))
        later, created, _ = self.ask_blocked()
        self.assertTrue(created)
        self.assertNotEqual(first.pk, later.pk)
        self.assertEqual((self.blocked_alternatives().count(), len(mail.outbox)), (2, 2))
        again, created_again, _ = self.ask_blocked()                              # el de hoy sí deduplica
        self.assertEqual((again.pk, created_again), (later.pk, False))
        self.assertEqual((self.blocked_alternatives().count(), len(mail.outbox)), (2, 2))

    def test_blocked_dedup_is_per_client(self):
        other = self.approved_nutrition(self.client_b)
        other_slot = other.nutrition.slots.get(code='lunch')
        other_option = other_slot.options.get(position=2)
        self.make_profile(self.client_a, pregnancy_status='pregnant')
        self.make_profile(self.client_b, pregnancy_status='pregnant')             # mismo bloqueo
        mail.outbox.clear()
        mine, created_mine, _ = self.ask_blocked()
        theirs, created_theirs, _ = self.ask_blocked(self.client_b, other_slot, other_option)
        self.assertEqual((created_mine, created_theirs), (True, True))
        self.assertNotEqual(mine.pk, theirs.pk)
        self.assertEqual((mine.client, theirs.client), (self.client_a, self.client_b))
        self.assertEqual(len(mail.outbox), 2)
        # Cada una deduplica solo con lo suyo
        again, created_again, _ = self.ask_blocked(self.client_b, other_slot, other_option)
        self.assertEqual((again.pk, created_again), (theirs.pk, False))
        self.assertEqual(len(mail.outbox), 2)

    def test_blocked_dedup_ignores_blocked_jobs_of_another_kind(self):
        self.make_profile(self.client_a, pregnancy_status='pregnant')
        plan_job, _ = self.request_nutrition(self.client_a)                      # plan completo: bloqueado, con su aviso
        self.assertEqual(plan_job.status, PlanGenerationJob.STATUS_BLOCKED)
        self.assertEqual(len(mail.outbox), 1)
        job, created, _ = self.ask_blocked()
        self.assertTrue(created)
        self.assertNotEqual(job.pk, plan_job.pk)
        self.assertEqual(job.kind, 'meal_alternative')
        self.assertEqual(len(mail.outbox), 2)

    def test_consent_revoked_means_not_ready(self):
        from apps.health.models import PURPOSE_AI
        health.revoke_consent(self.client_a, PURPOSE_AI)
        with self.assertRaises(services.PlanRequestNotReady):
            self.alternative()

    def test_trainer_keeps_or_reverts_the_new_option(self):
        self.alternative()
        new = self.slot.options.get(position=2, is_active=True)
        nutrition.review_option(self.plan, new, self.trainer_a, 'keep')
        new.refresh_from_db()
        self.assertFalse(new.needs_review)
        with self.assertRaises(services.PlanStateError):
            nutrition.review_option(self.plan, new, self.trainer_a, 'keep')            # ya revisada
        self.alternative(option=new)
        newer = self.slot.options.get(position=2, is_active=True)
        nutrition.review_option(self.plan, newer, self.trainer_a, 'revert')
        newer.refresh_from_db()
        self.assertFalse(newer.is_active)
        self.assertEqual(self.slot.options.get(position=2, is_active=True).pk, new.pk)
        with self.assertRaises(services.PlanNotAllowed):
            nutrition.review_option(self.plan, new, self.trainer_b, 'keep')

    def test_trainer_can_edit_a_client_option_in_an_approved_plan_only_while_pending_review(self):
        self.alternative()
        new = self.slot.options.get(position=2, is_active=True)
        items = [{'food_id': Food.objects.get(name='Pechuga de pollo cocida sin piel').id, 'grams': 200},
                 {'food_id': Food.objects.get(name='Arroz blanco cocido').id, 'grams': 200},
                 {'food_id': Food.objects.get(name='Tomate').id, 'grams': 120}]
        nutrition.edit_option(self.plan, new, self.trainer_a, 'Pollo con arroz', 'Cocina.', items)
        new.refresh_from_db()
        self.assertFalse(new.needs_review)
        self.assertEqual(Plan.objects.get(pk=self.plan.pk).status, Plan.STATUS_APPROVED)    # sigue aprobado
        with self.assertRaises(services.PlanStateError):
            nutrition.edit_option(self.plan, new, self.trainer_a, 'Otra vez', 'Cocina.', items)


class ChoiceTests(NutritionScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.plan = self.approved_nutrition(self.client_a)
        self.slot = self.plan.nutrition.slots.get(code='lunch')
        self.today = health.dates.today()

    def test_choose_and_change_the_same_day(self):
        first, second = self.slot.options.filter(position__in=(1, 2)).order_by('position')
        choice = nutrition.choose_option(self.client_a, self.slot.pk, first.pk, self.today)
        MealChoice.objects.filter(pk=choice.pk).update(completed_at=timezone.now())
        again = nutrition.choose_option(self.client_a, self.slot.pk, second.pk, self.today)
        self.assertEqual(choice.pk, again.pk)
        again.refresh_from_db()
        self.assertEqual((again.option, again.completed_at), (second, None))
        self.assertEqual(MealChoice.objects.count(), 1)

    def test_window_is_today_plus_six_days(self):
        option = self.slot.options.first()
        nutrition.choose_option(self.client_a, self.slot.pk, option.pk, self.today + timedelta(days=6))
        for day in (self.today - timedelta(days=1), self.today + timedelta(days=7)):
            with self.assertRaises(services.PlanStateError):
                nutrition.choose_option(self.client_a, self.slot.pk, option.pk, day)
        self.assertEqual(nutrition.parse_meal_date('2001-01-01'), self.today)
        self.assertEqual(nutrition.parse_meal_date('basura'), self.today)
        self.assertEqual(nutrition.parse_meal_date((self.today + timedelta(days=3)).isoformat()),
                         self.today + timedelta(days=3))

    def test_other_clients_options_are_not_found(self):
        other = self.approved_nutrition(self.client_b)
        foreign_slot = other.nutrition.slots.get(code='lunch')
        foreign_option = foreign_slot.options.first()
        for slot, option in ((foreign_slot, foreign_option), (self.slot, foreign_option),
                             (foreign_slot, self.slot.options.first())):
            with self.assertRaises(nutrition.OptionNotFound):
                nutrition.choose_option(self.client_a, slot.pk, option.pk, self.today)
        self.assertFalse(MealChoice.objects.exists())

    def test_day_view_totals_ignore_replaced_options(self):
        option = self.slot.options.get(position=1)
        nutrition.choose_option(self.client_a, self.slot.pk, option.pk, self.today)
        view = nutrition.day_view(self.client_a, self.plan, self.today)
        self.assertEqual(view['chosen_count'], 1)
        self.assertEqual(view['day_totals']['kcal'], int(round(option.kcal / 5) * 5))
        MealOption.objects.filter(pk=option.pk).update(is_active=False)
        self.assertIsNone(nutrition.day_view(self.client_a, self.plan, self.today)['day_totals'])

    def test_shopping_list_adds_up_the_week(self):
        rows, assumed = nutrition.shopping_list(self.client_a, self.plan)
        self.assertEqual(assumed, 3 * 7)                                            # nada elegido: rota A-B-C
        total = sum(row['grams'] for row in rows)
        expected = 0
        for slot in self.plan.nutrition.slots.all():
            options = list(slot.options.filter(is_active=True).order_by('position'))
            for day in range(7):
                expected += sum(i.grams for i in options[day % 3].items.all())
        self.assertEqual(total, expected)
        option = self.slot.options.get(position=1)
        for offset in range(7):
            nutrition.choose_option(self.client_a, self.slot.pk, option.pk, self.today + timedelta(days=offset))
        _rows, assumed = nutrition.shopping_list(self.client_a, self.plan)
        self.assertEqual(assumed, 2 * 7)

    def test_exchanges_respect_the_allergies(self):
        self.make_profile(self.client_a, allergies=['milk'], allergy_severity='mild')
        groups = nutrition.exchange_rows(self.client_a, self.plan)
        names = {row['food'].name for group in groups for row in group['rows']}
        self.assertTrue(names)
        self.assertNotIn('Leche entera', names)
        self.assertIn('Arroz blanco cocido', names)


class CompleteChoiceTests(NutritionScenarioTestCase):
    """Fase D: "Cumplí esta comida" (`nutrition.complete_choice`)."""

    def setUp(self):
        super().setUp()
        self.plan = self.approved_nutrition(self.client_a)
        self.slot = self.plan.nutrition.slots.get(code='lunch')
        self.option = self.slot.options.get(position=1)
        self.today = health.dates.today()

    def pick(self, day=None, client=None, slot=None, option=None):
        """Crea la elección directamente (`choose_option` no deja elegir días pasados)."""
        return MealChoice.objects.create(client=client or self.client_a, slot=slot or self.slot,
                                         option=option or self.option, date=day or self.today)

    def complete(self, choice, done=True, client=None):
        return nutrition.complete_choice(client or self.client_a, choice.pk, done)

    def completed_at(self, choice):
        choice.refresh_from_db()
        return choice.completed_at

    def test_mark_and_unmark(self):
        choice = self.pick()
        marked = self.complete(choice)
        self.assertEqual(marked.pk, choice.pk)
        self.assertIsNotNone(marked.completed_at)
        self.assertEqual(self.completed_at(choice), marked.completed_at)
        unmarked = self.complete(choice, done=False)
        self.assertIsNone(unmarked.completed_at)
        self.assertIsNone(self.completed_at(choice))

    def test_marking_twice_keeps_the_first_time(self):
        choice = self.pick()
        self.complete(choice)
        earlier = timezone.now() - timedelta(hours=3)
        MealChoice.objects.filter(pk=choice.pk).update(completed_at=earlier)
        again = self.complete(choice)                      # doble clic: no pisa la hora guardada
        self.assertEqual(again.completed_at, earlier)
        self.assertEqual(self.completed_at(choice), earlier)

    def test_unmarking_an_unmarked_meal_does_nothing(self):
        choice = self.pick()
        for _ in range(2):
            self.assertIsNone(self.complete(choice, done=False).completed_at)
        self.assertEqual(MealChoice.objects.count(), 1)

    def test_a_past_day_can_be_marked(self):
        choice = self.pick(day=self.today - timedelta(days=2))
        self.assertIsNotNone(self.complete(choice).completed_at)

    def test_a_future_day_cannot_be_marked_but_can_be_unmarked(self):
        choice = self.pick(day=self.today + timedelta(days=1))
        with self.assertRaises(services.PlanStateError) as ctx:
            self.complete(choice)
        self.assertEqual(str(ctx.exception), 'Solo puedes marcar comidas de hoy o de días anteriores.')
        self.assertIsNone(self.completed_at(choice))
        MealChoice.objects.filter(pk=choice.pk).update(completed_at=timezone.now())   # (marca vieja)
        self.assertIsNone(self.complete(choice, done=False).completed_at)

    def test_changing_the_option_the_same_day_resets_the_mark(self):
        first, second = self.slot.options.filter(position__in=(1, 2)).order_by('position')
        choice = nutrition.choose_option(self.client_a, self.slot.pk, first.pk, self.today)
        self.complete(choice)
        nutrition.choose_option(self.client_a, self.slot.pk, second.pk, self.today)
        self.assertIsNone(self.completed_at(choice))
        self.assertIsNotNone(self.complete(choice).completed_at)                       # y se puede volver a marcar
        nutrition.choose_option(self.client_a, self.slot.pk, second.pk, self.today)     # la misma opción: sin cambios
        self.assertIsNotNone(self.completed_at(choice))

    def test_an_incompatible_option_cannot_be_marked_but_can_be_unmarked(self):
        choice = self.pick()
        self.complete(choice)                                          # se marcó cuando todavía era compatible
        MealOptionItem.objects.filter(option=self.option).update(food=Food.objects.get(name='Leche entera'))
        self.make_profile(self.client_a, allergies=['milk'], allergy_severity='mild')
        self.assertIn(self.option.pk, nutrition.conflicting_option_ids(self.plan))
        self.assertIsNone(self.complete(choice, done=False).completed_at)
        with self.assertRaises(services.PlanStateError) as ctx:
            self.complete(choice)
        self.assertIn('ya no es compatible con tu ficha', str(ctx.exception))
        self.assertIsNone(self.completed_at(choice))

    def test_someone_elses_choice_is_not_found_and_stays_untouched(self):
        other = self.approved_nutrition(self.client_b)
        foreign_slot = other.nutrition.slots.get(code='lunch')
        foreign_option = foreign_slot.options.first()
        theirs = self.pick(client=self.client_b, slot=foreign_slot, option=foreign_option)
        known = timezone.now() - timedelta(days=1)
        MealChoice.objects.filter(pk=theirs.pk).update(completed_at=known)
        mine = self.pick()
        forged = self.pick(slot=foreign_slot, option=foreign_option)   # (suya, pero de una comida de otra clienta)
        for done in (True, False):
            for choice, client in ((theirs, self.client_a), (mine, self.client_b), (forged, self.client_a)):
                with self.subTest(done=done, choice=choice.pk, client=client.pk), self.assertRaises(nutrition.OptionNotFound):
                    self.complete(choice, done=done, client=client)
        with self.assertRaises(nutrition.OptionNotFound):
            nutrition.complete_choice(self.client_a, 999999, True)
        self.assertEqual(self.completed_at(theirs), known)
        self.assertIsNone(self.completed_at(mine))
        self.assertIsNone(self.completed_at(forged))

    def test_a_choice_owned_by_someone_else_on_my_own_plan_is_not_found(self):
        """Aunque el dato quede cruzado (elección de otra clienta sobre una comida de MI plan), la dueña
        es quien cuenta: ni se marca ni se desmarca."""
        known = timezone.now() - timedelta(hours=4)
        intruder = self.pick(client=self.client_b)
        MealChoice.objects.filter(pk=intruder.pk).update(completed_at=known)
        for done in (True, False):
            with self.subTest(done=done), self.assertRaises(nutrition.OptionNotFound):
                self.complete(intruder, done=done)
        self.assertEqual(self.completed_at(intruder), known)

    def test_a_choice_from_a_draft_plan_is_not_found(self):
        job, _ = self.request_nutrition(self.client_a)                 # 2.º plan, en borrador
        draft_slot = job.plan.nutrition.slots.get(code='lunch')
        choice = self.pick(slot=draft_slot, option=draft_slot.options.first())
        with self.assertRaises(nutrition.OptionNotFound):
            self.complete(choice)
        self.assertIsNone(self.completed_at(choice))
        # Y una clienta que solo tiene borrador no puede marcar nada
        draft_b = self.nutrition_draft(self.client_b)
        slot_b = draft_b.nutrition.slots.get(code='lunch')
        choice_b = self.pick(client=self.client_b, slot=slot_b, option=slot_b.options.first())
        with self.assertRaises(nutrition.OptionNotFound):
            self.complete(choice_b, client=self.client_b)

    def test_a_choice_from_an_archived_plan_is_not_found(self):
        choice = self.pick()
        services.archive_plan(self.plan, self.trainer_a, 'Cambió de objetivo')
        with self.assertRaises(nutrition.OptionNotFound):
            self.complete(choice)
        self.assertIsNone(self.completed_at(choice))

    def test_a_choice_from_a_superseded_plan_is_not_found(self):
        old = self.pick()
        job, _ = self.request_nutrition(self.client_a)
        Plan.objects.filter(pk=job.plan.pk).update(is_simulated=False)
        new_plan = services.approve_plan(Plan.objects.get(pk=job.plan.pk), self.boss)
        self.assertEqual(Plan.objects.get(pk=self.plan.pk).status, Plan.STATUS_SUPERSEDED)
        with self.assertRaises(nutrition.OptionNotFound):
            self.complete(old)
        new_slot = new_plan.nutrition.slots.get(code='lunch')
        current = self.pick(slot=new_slot, option=new_slot.options.first())
        self.assertIsNotNone(self.complete(current).completed_at)

    def test_a_replaced_option_is_not_found(self):
        choice = self.pick()
        MealOption.objects.filter(pk=self.option.pk).update(is_active=False)
        with self.assertRaises(nutrition.OptionNotFound):
            self.complete(choice)
        self.assertIsNone(self.completed_at(choice))

    def test_day_view_tells_the_template_what_can_be_done(self):
        lunch = self.pick()
        tomorrow = self.pick(day=self.today + timedelta(days=1))
        view = nutrition.day_view(self.client_a, self.plan, self.today)
        entries = {entry['slot'].code: entry for entry in view['slots']}
        self.assertEqual((entries['lunch']['choice_id'], entries['lunch']['completed'], entries['lunch']['can_complete']),
                         (lunch.pk, False, True))
        self.assertEqual((entries['lunch']['chosen_id'], view['chosen_count'], view['completed_count']),
                         (self.option.pk, 1, 0))                       # (las claves de siempre siguen ahí)
        for code in ('breakfast', 'dinner'):                          # sin elección: nada que marcar
            self.assertEqual((entries[code]['choice_id'], entries[code]['completed'], entries[code]['can_complete']),
                             (None, False, False))
        self.complete(lunch)
        view = nutrition.day_view(self.client_a, self.plan, self.today)
        entry = [e for e in view['slots'] if e['slot'].code == 'lunch'][0]
        self.assertEqual((entry['completed'], entry['can_complete'], view['completed_count']), (True, True, 1))
        # Mañana: hay elección pero todavía no se puede marcar; lo de hoy no cuenta
        view = nutrition.day_view(self.client_a, self.plan, self.today + timedelta(days=1))
        entry = [e for e in view['slots'] if e['slot'].code == 'lunch'][0]
        self.assertEqual((entry['choice_id'], entry['completed'], entry['can_complete'], view['completed_count']),
                         (tomorrow.pk, False, False, 0))

    def test_day_view_does_not_offer_to_mark_an_incompatible_option_but_shows_the_mark(self):
        choice = self.pick()
        self.complete(choice)
        MealOptionItem.objects.filter(option=self.option).update(food=Food.objects.get(name='Leche entera'))
        self.make_profile(self.client_a, allergies=['milk'], allergy_severity='mild')
        view = nutrition.day_view(self.client_a, self.plan, self.today)
        entry = [e for e in view['slots'] if e['slot'].code == 'lunch'][0]
        self.assertEqual((entry['completed'], entry['can_complete'], view['completed_count']), (True, False, 1))
