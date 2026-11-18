"""Casos numéricos independientes, aislamiento, consentimiento, CSRF y preservación histórica."""
import json
import uuid
from decimal import Decimal

from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse

from apps.accounts.tests.helpers import ScenarioTestCase
from apps.assessments.cardio import calculate_indices
from apps.assessments.models import CardioEvaluation, DixonTest
from apps.health import services as health_services
from apps.health.models import PURPOSE_HEALTH_DATA, ConsentTextVersion


class CalculationTests(SimpleTestCase):
    def test_distinguishes_ruffier_from_dickson(self):
        result = calculate_indices(60, 120, 80)
        self.assertEqual(result, {'ruffier_index': Decimal('6.00'), 'ruffier_dickson_index': Decimal('9.00')})

    def test_negative_and_boundary_indices_are_not_clamped(self):
        self.assertEqual(calculate_indices(60, 60, 60)['ruffier_dickson_index'], Decimal('-1.00'))
        self.assertEqual(calculate_indices(20, 250, 250)['ruffier_dickson_index'], Decimal('64.00'))

    def test_rejects_bool_decimal_and_invalid_values(self):
        for value in (True, 70.0, '70', None, 0, 19, 251, 999999999999999999):
            with self.subTest(value=value), self.assertRaises(ValueError):
                calculate_indices(value, 120, 80)


@override_settings(HEALTH_FEATURES_ENABLED=True)
class CardioAPITests(ScenarioTestCase):
    def setUp(self):
        self.login(self.trainer_a)
        self.url = reverse('api_ruffier_dickson', args=[self.assessment_a.pk])
        text = health_services.current_text(PURPOSE_HEALTH_DATA)
        health_services.grant_consent(self.client_a, PURPOSE_HEALTH_DATA, text.version)
        self.payload = {
            'resting_hr_bpm': 60, 'post_exercise_hr_bpm': 120, 'recovery_hr_bpm': 80,
            'protocol_confirmed': True, 'request_token': str(uuid.uuid4()),
        }

    def post(self, payload=None):
        return self.client.post(self.url, json.dumps(self.payload if payload is None else payload),
                                content_type='application/json')

    def test_create_server_calculates_and_records_actor_consent_and_versions(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual((data['ruffier_index'], data['ruffier_dickson_index']), ('6.00', '9.00'))
        self.assertEqual(data['interpretation_status'], 'professional_review_required')
        row = CardioEvaluation.objects.get()
        self.assertEqual(row.recorded_by, self.trainer_a)
        self.assertEqual(row.consent.user_id, self.client_a.pk)
        self.assertEqual(row.formula_version, 'ruffier-dickson/1')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('no-store', self.client.get(self.url)['Cache-Control'])

    def test_retry_is_idempotent_and_token_cannot_change_pulses(self):
        first = self.post()
        second = self.post()
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.json()['id'], second.json()['id'])
        self.payload['resting_hr_bpm'] = 61
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(CardioEvaluation.objects.count(), 1)

    def test_new_token_keeps_both_measurements_and_legacy_unchanged(self):
        legacy = DixonTest.objects.create(assessment=self.assessment_a, p0=60, p1=120, p2=80)
        original = (legacy.index_value, legacy.classification)
        self.post()
        self.payload.update(request_token=str(uuid.uuid4()), recovery_hr_bpm=90)
        self.post()
        self.assertEqual(CardioEvaluation.objects.count(), 2)
        legacy.refresh_from_db()
        self.assertEqual((legacy.index_value, legacy.classification), original)
        self.assertEqual(self.client.get(self.url).json()['results'][0]['recovery_hr_bpm'], 90)

    def test_authentication_role_and_assignment(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.login(self.client_a)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.login(self.trainer_b)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.post().status_code, 404)
        self.login(self.boss)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(CardioEvaluation.objects.count(), 0)

    def test_consent_revoked_or_new_text_blocks_reads_and_writes(self):
        self.post()
        health_services.revoke_consent(self.client_a, PURPOSE_HEALTH_DATA)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.post().status_code, 403)
        text = health_services.current_text(PURPOSE_HEALTH_DATA)
        health_services.grant_consent(self.client_a, PURPOSE_HEALTH_DATA, text.version)
        ConsentTextVersion.objects.filter(pk=text.pk).update(is_current=False)
        ConsentTextVersion.objects.create(purpose=PURPOSE_HEALTH_DATA, version=text.version + 1,
                                          body='Nueva versión', is_current=True)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.post().status_code, 403)

    def test_minor_and_disabled_feature(self):
        self.assessment_a.age = 17
        self.assessment_a.save()
        self.assertEqual(self.post().status_code, 403)
        with override_settings(HEALTH_FEATURES_ENABLED=False):
            self.assertEqual(self.client.get(self.url).status_code, 404)
            self.assertEqual(self.post().status_code, 404)

    def test_strict_validation_and_protected_fields(self):
        for field, value in (
            ('resting_hr_bpm', True), ('post_exercise_hr_bpm', 120.5), ('recovery_hr_bpm', '80'),
            ('resting_hr_bpm', 251), ('resting_hr_bpm', None),
            ('request_token', 'bad'), ('protocol_confirmed', False), ('protocol_confirmed', 'true'),
            ('ruffier_index', 0), ('recorded_by', self.boss.pk), ('formula_version', 'fake'),
        ):
            with self.subTest(field=field, value=value):
                payload = {**self.payload, field: value}
                self.assertEqual(self.post(payload).status_code, 400)
        for payload in ([], None, {'request_token': str(uuid.uuid4())}):
            response = self.client.post(self.url, json.dumps(payload), content_type='application/json')
            self.assertEqual(response.status_code, 400)
        self.assertEqual(CardioEvaluation.objects.count(), 0)

    def test_http_errors_do_not_write(self):
        self.assertEqual(self.client.post(self.url, '{', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post(self.url, b'\xff', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post(self.url, 'x' * 4097, content_type='application/json').status_code, 413)
        self.assertEqual(self.client.post(self.url, {}).status_code, 415)
        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response['Allow'], 'GET, POST')
        self.assertEqual(CardioEvaluation.objects.count(), 0)

    def test_csrf_is_enforced_and_valid_same_origin_post_succeeds(self):
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.trainer_a, backend='django.contrib.auth.backends.ModelBackend')
        response = browser.post(self.url, json.dumps(self.payload), content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error']['code'], 'csrf_failed')
        self.assertIn('no-store', response['Cache-Control'])
        page = browser.get(reverse('trainer_assessment_detail', args=[self.assessment_a.pk]))
        self.assertContains(page, 'data-cardio-api')
        token = browser.cookies['csrftoken'].value
        response = browser.post(self.url, json.dumps(self.payload), content_type='application/json',
                                HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 201, response.content)

    def test_pagination_is_bounded_and_authorized(self):
        for _ in range(21):
            self.payload['request_token'] = str(uuid.uuid4())
            self.post()
        page1 = self.client.get(self.url).json()
        self.assertEqual(len(page1['results']), 20)
        page2 = self.client.get(self.url, {'before': page1['next_before']}).json()
        self.assertEqual(len(page2['results']), 1)
        self.assertIsNone(page2['next_before'])
        self.assertEqual(self.client.get(self.url, {'before': 'bad'}).status_code, 400)

    def test_new_measurements_are_in_own_health_export_only(self):
        self.post()
        exported = health_services.export_data(self.client_a)
        self.assertEqual(len(exported['cardio_evaluations']), 1)
        self.assertEqual(health_services.export_data(self.client_b)['cardio_evaluations'], [])

    def test_legacy_ui_is_labeled_ruffier_and_new_panel_is_hidden_when_disabled(self):
        with override_settings(HEALTH_FEATURES_ENABLED=False):
            page = self.client.get(reverse('trainer_assessment_detail', args=[self.assessment_a.pk]))
        self.assertNotContains(page, 'data-cardio-api')
        self.assertContains(page, 'Índice de Ruffier (registro anterior)')
        self.assertIn('no-store', page['Cache-Control'])

