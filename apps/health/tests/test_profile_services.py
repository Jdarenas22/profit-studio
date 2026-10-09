"""Casos de uso de la ficha: versiones, confirmación, consentimiento, menores, exportar, borrar."""
import json
from datetime import timedelta
from unittest import mock

from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.contrib import admin

from apps.accounts.models import User
from apps.assessments.models import BodyMeasurement, InitialAssessment
from apps.health import dates, services
from apps.health.models import (
    PURPOSE_AI, PURPOSE_HEALTH_DATA, HealthAccessLog, HealthProfile,
)
from apps.plans.rules.flags import add_months

from .helpers import HealthScenarioTestCase, adult_birth_date, cleaned_profile


class SaveProfileTests(HealthScenarioTestCase):
    def test_member_saves_first_version(self):
        self.grant(self.client_a)
        profile = services.save_profile(self.client_a, cleaned_profile(), actor=self.client_a)
        self.assertEqual(profile.version, 1)
        self.assertTrue(profile.is_current)
        self.assertEqual(profile.created_by_role, 'member')
        self.assertEqual(profile.created_by, self.client_a)
        self.assertIsNotNone(profile.confirmed_by_client_at)
        self.assertEqual(profile.consent, services.active_consent(self.client_a, PURPOSE_HEALTH_DATA))
        self.assertEqual(profile.meal_slots, ['breakfast', 'lunch', 'dinner'])
        self.assertEqual(profile.parq['q1'], False)

    def test_goal_is_stored_in_user_not_duplicated(self):
        self.grant(self.client_a)
        services.save_profile(self.client_a, cleaned_profile(training_goal='muscle_gain'), actor=self.client_a)
        self.client_a.refresh_from_db()
        self.assertEqual(self.client_a.training_goal, 'muscle_gain')
        self.assertFalse(hasattr(HealthProfile, 'training_goal'))

    def test_every_save_creates_a_version_and_keeps_one_current(self):
        for expected in (1, 2, 3):
            profile = self.make_profile(self.client_a)
            self.assertEqual(profile.version, expected)
        versions = HealthProfile.objects.filter(user=self.client_a).order_by('version')
        self.assertEqual([p.version for p in versions], [1, 2, 3])
        self.assertEqual([p.is_current for p in versions], [False, False, True])
        self.assertEqual(services.current_profile(self.client_a).version, 3)
        # La de otra clienta no se toca
        other = self.make_profile(self.client_b)
        self.assertEqual(other.version, 1)
        self.assertTrue(other.is_current)

    def test_old_versions_keep_their_content(self):
        self.make_profile(self.client_a, liked_foods_text='arepa')
        self.make_profile(self.client_a, liked_foods_text='avena')
        first = HealthProfile.objects.get(user=self.client_a, version=1)
        self.assertEqual(first.liked_foods_text, 'arepa')

    def test_requires_health_data_consent(self):
        with self.assertRaises(services.ProfileConsentRequired):
            services.save_profile(self.client_a, cleaned_profile(), actor=self.client_a)
        self.assertEqual(HealthProfile.objects.count(), 0)

    def test_ai_consent_alone_is_not_enough_and_is_not_needed(self):
        self.grant(self.client_a, PURPOSE_AI)
        with self.assertRaises(services.ProfileConsentRequired):
            services.save_profile(self.client_a, cleaned_profile(), actor=self.client_a)
        self.grant(self.client_a, PURPOSE_HEALTH_DATA)
        self.client_b.refresh_from_db()
        # client_b solo tiene health_data (sin consentimiento de IA) y guarda sin problema
        self.grant(self.client_b, PURPOSE_HEALTH_DATA)
        services.save_profile(self.client_b, cleaned_profile(), actor=self.client_b)
        self.assertFalse(services.has_valid_consent(self.client_b, PURPOSE_AI))

    def test_consent_with_old_text_version_is_not_enough(self):
        self.grant(self.client_a)
        self.publish_new_version(PURPOSE_HEALTH_DATA)
        with self.assertRaises(services.ProfileConsentRequired):
            services.save_profile(self.client_a, cleaned_profile(), actor=self.client_a)

    def test_revoked_consent_blocks_saving(self):
        self.grant(self.client_a)
        services.revoke_consent(self.client_a, PURPOSE_HEALTH_DATA)
        with self.assertRaises(services.ProfileConsentRequired):
            services.save_profile(self.client_a, cleaned_profile(), actor=self.client_a)

    def test_service_rejects_minors_even_if_the_form_was_skipped(self):
        self.grant(self.client_a)
        data = cleaned_profile()
        data['birth_date'] = dates.today() - timedelta(days=365 * 15)
        with self.assertRaises(services.ProfileInvalid):
            services.save_profile(self.client_a, data, actor=self.client_a)
        data['birth_date'] = add_months(dates.today(), -12 * 18) + timedelta(days=1)    # le faltan 1 día
        with self.assertRaises(services.ProfileInvalid):
            services.save_profile(self.client_a, data, actor=self.client_a)
        data['birth_date'] = add_months(dates.today(), -12 * 18)                         # cumple 18 hoy
        services.save_profile(self.client_a, data, actor=self.client_a)
        data['birth_date'] = add_months(dates.today(), -12 * 101)
        with self.assertRaises(services.ProfileInvalid):
            services.save_profile(self.client_a, data, actor=self.client_a)

    def test_service_requires_breakfast_lunch_dinner(self):
        self.grant(self.client_a)
        for slots in (['breakfast', 'lunch'], ['lunch', 'snack', 'dinner'], [],
                      ['breakfast', 'lunch', 'dinner', 'dinner'], ['breakfast', 'lunch', 'dinner', 'x']):
            data = cleaned_profile()
            data['meal_slots'] = slots
            with self.assertRaises(services.ProfileInvalid, msg=str(slots)):
                services.save_profile(self.client_a, data, actor=self.client_a)
        data = cleaned_profile(meal_slots=['breakfast', 'mid_morning', 'lunch', 'snack', 'dinner'])
        self.assertEqual(len(services.save_profile(self.client_a, data, actor=self.client_a).meal_slots), 5)

    def test_missing_keys_and_bad_goal_are_rejected(self):
        self.grant(self.client_a)
        data = cleaned_profile()
        del data['parq']
        with self.assertRaises(services.ProfileInvalid):
            services.save_profile(self.client_a, data, actor=self.client_a)
        data = cleaned_profile()
        data['training_goal'] = 'volar'
        with self.assertRaises(services.ProfileInvalid):
            services.save_profile(self.client_a, data, actor=self.client_a)
        self.assertEqual(HealthProfile.objects.count(), 0)

    def test_client_cannot_save_for_someone_else_and_trainers_do_not_have_a_profile(self):
        self.grant(self.client_a)
        with self.assertRaises(services.ProfileNotAllowed):
            services.save_profile(self.client_a, cleaned_profile(), actor=self.client_b)
        with self.assertRaises(services.ProfileNotAllowed):
            services.save_profile(self.trainer_a, cleaned_profile(), actor=self.trainer_a)
        self.assertEqual(HealthProfile.objects.count(), 0)

    def test_conflict_rolls_everything_back(self):
        first = self.make_profile(self.client_a)
        with mock.patch.object(HealthProfile.objects, 'create', side_effect=IntegrityError('carrera')):
            with self.assertRaises(services.ProfileConflict):
                services.save_profile(self.client_a, cleaned_profile(), actor=self.client_a)
        first.refresh_from_db()
        self.assertTrue(first.is_current)                      # la vigente sigue siendo la misma
        self.assertEqual(HealthProfile.objects.filter(user=self.client_a).count(), 1)

    def test_database_enforces_single_current_and_unique_version(self):
        profile = self.make_profile(self.client_a)
        clone = HealthProfile.objects.get(pk=profile.pk)
        clone.pk = None
        clone.version = 2
        with self.assertRaises(IntegrityError), transaction.atomic():
            clone.save()                                       # dos vigentes
        clone.is_current = False
        clone.version = 1
        with self.assertRaises(IntegrityError), transaction.atomic():
            clone.save()                                       # versión repetida


class TrainerCorrectionTests(HealthScenarioTestCase):
    def test_trainer_correction_is_a_new_unconfirmed_version(self):
        self.make_profile(self.client_a)
        profile = services.save_profile(
            self.client_a, cleaned_profile(injuries=['knee'], training_goal='rehab'), actor=self.trainer_a)
        self.assertEqual(profile.version, 2)
        self.assertEqual(profile.created_by_role, 'trainer')
        self.assertEqual(profile.created_by, self.trainer_a)
        self.assertIsNone(profile.confirmed_by_client_at)
        self.assertTrue(profile.needs_confirmation)
        self.assertFalse(HealthProfile.objects.get(user=self.client_a, version=1).is_current)
        self.client_a.refresh_from_db()
        self.assertEqual(self.client_a.training_goal, 'rehab')
        entry = HealthAccessLog.objects.get(client=self.client_a, action='edit')
        self.assertEqual((entry.actor, entry.profile_version), (self.trainer_a, 2))

    def test_client_save_after_correction_is_confirmed_again(self):
        self.make_profile(self.client_a)
        services.save_profile(self.client_a, cleaned_profile(), actor=self.trainer_a)
        profile = self.make_profile(self.client_a)
        self.assertEqual(profile.version, 3)
        self.assertIsNotNone(profile.confirmed_by_client_at)

    def test_trainer_cannot_create_the_first_profile(self):
        self.grant(self.client_a)
        with self.assertRaises(services.ProfileNotFound):
            services.save_profile(self.client_a, cleaned_profile(), actor=self.trainer_a)
        with self.assertRaises(services.ProfileNotFound):
            services.save_profile(self.client_a, cleaned_profile(), actor=self.boss)
        self.assertEqual(HealthProfile.objects.count(), 0)

    def test_trainer_needs_the_clients_consent(self):
        self.make_profile(self.client_a)
        services.revoke_consent(self.client_a, PURPOSE_HEALTH_DATA)
        with self.assertRaises(services.ProfileConsentRequired):
            services.save_profile(self.client_a, cleaned_profile(), actor=self.trainer_a)
        self.assertEqual(HealthProfile.objects.filter(user=self.client_a).count(), 1)
        self.assertFalse(HealthAccessLog.objects.exists())

    def test_assignment_rules(self):
        self.make_profile(self.client_a)
        self.make_profile(self.client_free)
        # Otro entrenador: no
        with self.assertRaises(services.ProfileNotAllowed):
            services.save_profile(self.client_a, cleaned_profile(), actor=self.trainer_b)
        # Cliente sin asignar: solo la superusuaria
        with self.assertRaises(services.ProfileNotAllowed):
            services.save_profile(self.client_free, cleaned_profile(), actor=self.trainer_a)
        services.save_profile(self.client_free, cleaned_profile(), actor=self.boss)
        services.save_profile(self.client_a, cleaned_profile(), actor=self.boss)
        self.assertEqual(HealthAccessLog.objects.filter(actor=self.boss).count(), 2)

    def test_trainer_cannot_save_a_minor(self):
        self.make_profile(self.client_a)
        data = cleaned_profile()
        data['birth_date'] = dates.today() - timedelta(days=365 * 16)
        with self.assertRaises(services.ProfileInvalid):
            services.save_profile(self.client_a, data, actor=self.trainer_a)


class ConfirmTests(HealthScenarioTestCase):
    def test_confirm_marks_the_current_version(self):
        self.make_profile(self.client_a)
        corrected = services.save_profile(self.client_a, cleaned_profile(), actor=self.trainer_a)
        profile, changed = services.confirm_profile(self.client_a)
        self.assertTrue(changed)
        self.assertEqual(profile.pk, corrected.pk)
        self.assertIsNotNone(profile.confirmed_by_client_at)

    def test_confirm_is_idempotent(self):
        self.make_profile(self.client_a)
        first, _ = services.confirm_profile(self.client_a)
        stamp = first.confirmed_by_client_at
        again, changed = services.confirm_profile(self.client_a)
        self.assertFalse(changed)
        self.assertEqual(again.confirmed_by_client_at, stamp)

    def test_confirm_needs_profile_and_consent(self):
        with self.assertRaises(services.ProfileConsentRequired):
            services.confirm_profile(self.client_a)
        self.grant(self.client_a)
        with self.assertRaises(services.ProfileNotFound):
            services.confirm_profile(self.client_a)
        self.make_profile(self.client_a)
        services.save_profile(self.client_a, cleaned_profile(), actor=self.trainer_a)
        services.revoke_consent(self.client_a, PURPOSE_HEALTH_DATA)
        with self.assertRaises(services.ProfileConsentRequired):
            services.confirm_profile(self.client_a)
        self.assertIsNone(services.current_profile(self.client_a).confirmed_by_client_at)


class IsMinorTests(HealthScenarioTestCase):
    def test_without_profile_the_assessment_age_is_the_fallback(self):
        self.assertFalse(services.is_minor(self.client_a))                 # valoración: 30 años
        InitialAssessment.objects.filter(user=self.client_a).update(age=15)
        self.assertTrue(services.is_minor(self.client_a))
        self.assertFalse(services.is_minor(self.new_member('sin_datos')))  # sin ningún dato

    def test_profile_birth_date_wins_over_the_assessment(self):
        InitialAssessment.objects.filter(user=self.client_a).update(age=15)
        self.make_profile(self.client_a)                                   # nació hace ~30 años
        self.assertFalse(services.is_minor(self.client_a))
        InitialAssessment.objects.filter(user=self.client_a).update(age=40)
        HealthProfile.objects.filter(user=self.client_a).update(birth_date=dates.today() - timedelta(days=365 * 10))
        self.assertTrue(services.is_minor(self.client_a))

    def test_border_at_18(self):
        self.make_profile(self.client_a)
        today = dates.today()
        HealthProfile.objects.filter(user=self.client_a).update(birth_date=add_months(today, -12 * 18))
        self.assertFalse(services.is_minor(self.client_a))
        HealthProfile.objects.filter(user=self.client_a).update(
            birth_date=add_months(today, -12 * 18) + timedelta(days=1))
        self.assertTrue(services.is_minor(self.client_a))


class DeleteAndExportTests(HealthScenarioTestCase):
    def test_delete_removes_every_version_and_only_hers(self):
        for _ in range(3):
            self.make_profile(self.client_a)
        self.make_profile(self.client_b)
        self.assertEqual(services.delete_profile(self.client_a), 3)
        self.assertFalse(HealthProfile.objects.filter(user=self.client_a).exists())
        self.assertTrue(HealthProfile.objects.filter(user=self.client_b, is_current=True).exists())
        entry = HealthAccessLog.objects.get(client=self.client_a, action='delete')
        self.assertEqual((entry.actor, entry.profile_version), (self.client_a, 3))

    def test_log_survives_the_deletion_and_second_delete_does_nothing(self):
        self.make_profile(self.client_a)
        services.delete_profile(self.client_a)
        self.assertEqual(services.delete_profile(self.client_a), 0)
        self.assertEqual(HealthAccessLog.objects.filter(client=self.client_a, action='delete').count(), 1)

    def test_consent_is_not_revoked_by_deleting(self):
        self.make_profile(self.client_a)
        services.delete_profile(self.client_a)
        self.assertTrue(services.has_health_consent(self.client_a))

    def test_after_delete_a_new_profile_starts_again(self):
        self.make_profile(self.client_a)
        services.delete_profile(self.client_a)
        self.assertEqual(self.make_profile(self.client_a).version, 1)

    def test_export_has_only_own_data_and_is_json(self):
        self.make_profile(self.client_a, other_condition_text='SECRETO-A', conditions=['other'])
        self.make_profile(self.client_b, other_condition_text='SECRETO-B', conditions=['other'])
        BodyMeasurement.objects.create(user=self.client_b, weight=77, source='member', notes='NOTA-B')
        payload = services.export_data(self.client_a)
        text = json.dumps(payload, cls=DjangoJSONEncoder, ensure_ascii=False)
        self.assertIn('SECRETO-A', text)
        self.assertNotIn('SECRETO-B', text)
        self.assertNotIn('NOTA-B', text)
        self.assertNotIn(self.client_b.username, text)
        self.assertEqual(payload['format'], 'profit-studio-health-export/1')
        self.assertEqual(len(payload['health_profiles']), 1)
        self.assertEqual(payload['account']['username'], 'client_a')
        self.assertTrue(payload['body_measurements'])
        self.assertEqual(payload['consents'][0]['purpose'], 'health_data')
        self.assertNotIn('password', text)

    def test_export_is_logged_and_listed(self):
        self.make_profile(self.client_a)
        services.export_data(self.client_a)
        entry = HealthAccessLog.objects.get(client=self.client_a, action='export')
        self.assertEqual((entry.actor, entry.profile_version), (self.client_a, 1))
        later = services.export_data(self.client_a)
        self.assertTrue(any(item['action'] == 'export' for item in later['access_log']))

    def test_export_without_profile_still_works(self):
        payload = services.export_data(self.client_a)
        self.assertEqual(payload['health_profiles'], [])


class FlagsAdapterTests(HealthScenarioTestCase):
    def test_flags_use_profile_measurements_and_goal(self):
        self.make_profile(self.client_a, conditions=['cancer_active'], training_goal='weight_loss')
        report = services.profile_flags(self.client_a)
        self.assertIn('R12_CANCER', report.codes)
        self.assertEqual(report.blocked_scopes, {'exercise', 'nutrition'})
        self.assertIn('M09_NO_AI_CONSENT', report.codes)          # no dio el consentimiento de IA
        self.assertNotIn('M01_NO_MEASURE', report.codes)          # hay una medición (60 kg)
        self.assertNotIn('M03_NO_HEIGHT', report.codes)           # estatura de la valoración inicial

    def test_ai_consent_removes_m09(self):
        self.make_profile(self.client_a)
        self.grant(self.client_a, PURPOSE_AI)
        self.assertNotIn('M09_NO_AI_CONSENT', services.profile_flags(self.client_a).codes)

    def test_age_comes_from_birth_date_and_mismatch_is_informative(self):
        self.make_profile(self.client_a, birth_date=adult_birth_date(41).isoformat())
        report = services.profile_flags(self.client_a)           # la valoración dice 30 años
        self.assertIn('I02_AGE_MISMATCH', report.codes)

    def test_unconfirmed_profile_is_reported(self):
        self.make_profile(self.client_a)
        services.save_profile(self.client_a, cleaned_profile(), actor=self.trainer_a)
        self.assertIn('M04_PROFILE_UNCONFIRMED', services.profile_flags(self.client_a).codes)

    def test_no_profile(self):
        report = services.profile_flags(self.client_a)
        self.assertEqual(report.codes.count('M04_NO_PROFILE'), 1)
        self.assertFalse(report.has_red)

    def test_old_measurement_is_reported(self):
        self.make_profile(self.client_a)
        BodyMeasurement.objects.filter(user=self.client_a).update(date=dates.today() - timedelta(days=45))
        self.assertIn('M02_MEASURE_OLD', services.profile_flags(self.client_a).codes)


class ModelAndAdminTests(HealthScenarioTestCase):
    def test_health_models_are_not_in_the_admin(self):
        registered = {model.__name__ for model in admin.site._registry}
        self.assertNotIn('HealthProfile', registered)
        self.assertNotIn('HealthAccessLog', registered)
        self.assertNotIn('ConsentRecord', registered)

    def test_profile_helpers(self):
        profile = self.make_profile(
            self.client_a, conditions=['asthma'], injuries=['knee'], allergies=['peanut'],
            allergy_severity='mild', parq_q3='yes', meal_slots=['breakfast', 'snack', 'lunch', 'dinner'],
            meal_time_snack='16:00', training_place='home', equipment=['mat', 'bands'])
        self.assertEqual(profile.conditions_labels, ['Asma'])
        self.assertEqual(profile.injuries_labels, ['Rodilla'])
        self.assertEqual(profile.allergies_labels, ['Maní'])
        self.assertEqual(profile.equipment_labels, ['Bandas elásticas', 'Colchoneta'])
        self.assertEqual([row['label'] for row in profile.meal_schedule],
                         ['Desayuno', 'Almuerzo', 'Merienda', 'Cena'])
        self.assertEqual({row['code']: row['time'] for row in profile.meal_schedule}['snack'], '16:00')
        self.assertEqual(profile.parq_yes_count, 1)
        self.assertEqual(len(profile.parq_answers), 7)
        self.assertTrue(profile.parq_answers[2]['answer'])
        self.assertEqual(profile.sex_label, 'Femenino')
        self.assertGreaterEqual(profile.age, 30)

    def test_clearance_is_valid_for_less_than_12_months(self):
        profile = self.make_profile(self.client_a)
        today = dates.today()
        profile.medical_clearance = True
        profile.clearance_date = add_months(today, -12) + timedelta(days=1)
        self.assertTrue(profile.clearance_is_valid())
        profile.clearance_date = add_months(today, -12)
        self.assertFalse(profile.clearance_is_valid())
        profile.medical_clearance = False
        profile.clearance_date = today
        self.assertFalse(profile.clearance_is_valid())

    def test_deleting_the_account_removes_profile_and_log(self):
        self.make_profile(self.client_a)
        services.export_data(self.client_a)
        User.objects.filter(pk=self.client_a.pk).delete()
        self.assertFalse(HealthProfile.objects.exists())
        self.assertFalse(HealthAccessLog.objects.exists())

    def test_trainer_deleted_keeps_the_log_entry(self):
        self.make_profile(self.client_a)
        services.save_profile(self.client_a, cleaned_profile(), actor=self.trainer_a)
        User.objects.filter(pk=self.trainer_a.pk).delete()
        entry = HealthAccessLog.objects.get(client=self.client_a, action='edit')
        self.assertIsNone(entry.actor)

    def test_consent_cards(self):
        self.grant(self.client_a)
        cards = {card['purpose']: card for card in services.consent_cards(self.client_a)}
        self.assertTrue(cards['health_data']['active'])
        self.assertEqual(cards['health_data']['version'], 1)
        self.assertFalse(cards['ai_processing']['active'])
        self.publish_new_version(PURPOSE_HEALTH_DATA)
        cards = {card['purpose']: card for card in services.consent_cards(self.client_a)}
        self.assertFalse(cards['health_data']['active'])
        self.assertTrue(cards['health_data']['outdated'])
        self.assertEqual(cards['health_data']['current_version'], 2)
