"""Consentimiento de datos de salud / IA: registro, versiones, retiro, acceso y textos."""
from datetime import date, timedelta

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.test import Client, override_settings
from django.urls import reverse

from apps.assessments.models import InitialAssessment
from apps.health import services
from apps.health.models import (
    PURPOSE_AI, PURPOSE_HEALTH_DATA, ConsentRecord, ConsentTextVersion, find_placeholders,
)
from apps.memberships.models import Membership

from .helpers import HealthScenarioTestCase, stub_missing_templates


@stub_missing_templates()
class ConsentAccessTests(HealthScenarioTestCase):
    def url(self, purpose=PURPOSE_HEALTH_DATA, name='member_consent'):
        return reverse(name, args=[purpose])

    def test_anonymous_goes_to_login(self):
        for name in ('member_consent', 'member_consent_revoke'):
            response = self.client.get(self.url(name=name))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn('/accounts/login/', response['Location'])

    def test_trainers_and_boss_get_403(self):
        for user in (self.trainer_a, self.boss):
            self.login(user)
            self.assertEqual(self.client.get(self.url()).status_code, 403, user.username)
            response = self.client.post(self.url(), {'accept': 'on', 'text_version': '1'})
            self.assertEqual(response.status_code, 403)
        self.assertEqual(ConsentRecord.objects.count(), 0)

    def test_member_without_membership_is_stopped(self):
        user = self.new_member('sin_membresia', with_membership=False)
        self.login(user)
        response = self.client.get(self.url())
        self.assertTemplateUsed(response, 'accounts/no_membership.html')

    def test_member_with_expired_membership_is_stopped(self):
        Membership.objects.filter(user=self.client_a).update(end_date=date.today() - timedelta(days=1))
        self.login(self.client_a)
        response = self.client.get(self.url())
        self.assertTemplateUsed(response, 'accounts/membership_expired.html')
        response = self.client.post(self.url(), {'accept': 'on', 'text_version': '1'})
        self.assertEqual(ConsentRecord.objects.count(), 0)

    def test_unknown_purpose_is_404(self):
        self.login(self.client_a)
        self.assertEqual(self.client.get(self.url('otra_cosa')).status_code, 404)
        self.assertEqual(self.client.post(self.url('otra_cosa', 'member_consent_revoke')).status_code, 404)

    def test_feature_flag_off_hides_everything(self):
        self.login(self.client_a)
        with override_settings(HEALTH_FEATURES_ENABLED=False):
            self.assertEqual(self.client.get(self.url()).status_code, 404)
            response = self.client.post(self.url(), {'accept': 'on', 'text_version': '1'})
            self.assertEqual(response.status_code, 404)
        self.assertEqual(ConsentRecord.objects.count(), 0)

    def test_responses_are_no_store(self):
        self.login(self.client_a)
        response = self.client.get(self.url())
        self.assertIn('no-store', response['Cache-Control'])

    def test_post_requires_csrf_token(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.client_a, backend='django.contrib.auth.backends.ModelBackend')
        response = strict.post(self.url(), {'accept': 'on', 'text_version': '1'})
        self.assertEqual(response.status_code, 403)
        response = strict.post(self.url(name='member_consent_revoke'))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ConsentRecord.objects.count(), 0)

    def test_minor_cannot_consent(self):
        InitialAssessment.objects.filter(user=self.client_a).update(age=15)
        self.login(self.client_a)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        response = self.client.post(self.url(), {'accept': 'on', 'text_version': '1'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ConsentRecord.objects.count(), 0)

    def test_adult_by_assessment_can_consent(self):
        InitialAssessment.objects.filter(user=self.client_a).update(age=18)
        self.login(self.client_a)
        self.assertEqual(self.client.get(self.url()).status_code, 200)


class ConsentFlowTests(HealthScenarioTestCase):
    def setUp(self):
        self.login(self.client_a)
        self.text = services.current_text(PURPOSE_HEALTH_DATA)

    def accept(self, **over):
        data = {'accept': 'on', 'text_version': str(self.text.version)}
        data.update(over)
        return self.client.post(reverse('member_consent', args=[PURPOSE_HEALTH_DATA]), data)

    def test_initial_texts_exist_and_keep_markers(self):
        for purpose in (PURPOSE_HEALTH_DATA, PURPOSE_AI):
            text = services.current_text(purpose)
            self.assertEqual(text.version, 1)
            self.assertIn('[RAZÓN SOCIAL]', text.placeholders)
            self.assertIn('[NIT]', text.placeholders)
            self.assertIn('[CORREO DE DERECHOS]', text.placeholders)

    def test_get_context_for_the_template(self):
        response = self.client.get(reverse('member_consent', args=[PURPOSE_HEALTH_DATA]))
        self.assertEqual(response.status_code, 200)
        ctx = response.context
        self.assertEqual(ctx['purpose'], PURPOSE_HEALTH_DATA)
        self.assertEqual(ctx['text_version'], 1)
        self.assertEqual(ctx['body'], self.text.body)
        self.assertFalse(ctx['already_granted'])
        self.assertIsNone(ctx['granted_at'])

    def test_accept_records_user_version_and_date(self):
        response = self.accept()
        self.assertRedirects(response, reverse('member_measurement_list'), fetch_redirect_response=False)
        record = ConsentRecord.objects.get()
        self.assertEqual((record.user, record.text_version, record.revoked_at),
                         (self.client_a, self.text, None))
        self.assertIsNotNone(record.granted_at)
        ctx = self.client.get(reverse('member_consent', args=[PURPOSE_HEALTH_DATA])).context
        self.assertTrue(ctx['already_granted'])
        self.assertEqual(ctx['granted_at'], record.granted_at)

    def test_without_checkbox_nothing_is_saved(self):
        response = self.client.post(reverse('member_consent', args=[PURPOSE_HEALTH_DATA]),
                                    {'text_version': str(self.text.version)})
        self.assertEqual(response.status_code, 200)
        self.assertIn('accept', response.context['errors'])
        self.assertEqual(ConsentRecord.objects.count(), 0)

    def test_checkbox_value_must_be_on(self):
        self.assertEqual(self.accept(accept='no').status_code, 200)
        self.assertEqual(ConsentRecord.objects.count(), 0)

    def test_stale_or_garbage_version_is_rejected(self):
        for bad in ('99', 'x', ''):
            response = self.accept(text_version=bad)
            self.assertEqual(response.status_code, 200, bad)
        self.assertEqual(ConsentRecord.objects.count(), 0)

    def test_double_submit_does_not_duplicate(self):
        self.accept()
        self.accept()
        self.assertEqual(ConsentRecord.objects.count(), 1)

    def test_one_purpose_does_not_grant_the_other(self):
        self.accept()
        self.assertTrue(services.has_valid_consent(self.client_a, PURPOSE_HEALTH_DATA))
        self.assertFalse(services.has_valid_consent(self.client_a, PURPOSE_AI))

    def test_new_text_version_requires_consent_again(self):
        self.accept()
        self.publish_new_version(PURPOSE_HEALTH_DATA)
        self.assertFalse(services.has_valid_consent(self.client_a, PURPOSE_HEALTH_DATA))
        # la versión que tenía en pantalla ya no es la vigente
        self.assertEqual(self.accept().status_code, 200)
        self.assertEqual(ConsentRecord.objects.count(), 1)
        new = services.current_text(PURPOSE_HEALTH_DATA)
        self.assertEqual(new.version, 2)
        self.accept(text_version='2')
        self.assertEqual(ConsentRecord.objects.count(), 2)   # se conserva el historial
        self.assertTrue(services.has_valid_consent(self.client_a, PURPOSE_HEALTH_DATA))

    def test_revoke_keeps_history_and_blocks_use(self):
        self.accept()
        response = self.client.post(reverse('member_consent_revoke', args=[PURPOSE_HEALTH_DATA]))
        self.assertEqual(response.status_code, 302)
        record = ConsentRecord.objects.get()
        self.assertIsNotNone(record.revoked_at)
        self.assertFalse(services.has_valid_consent(self.client_a, PURPOSE_HEALTH_DATA))
        # puede volver a autorizar: queda un registro nuevo
        self.accept()
        self.assertEqual(ConsentRecord.objects.count(), 2)

    def test_revoke_requires_post(self):
        self.assertEqual(
            self.client.get(reverse('member_consent_revoke', args=[PURPOSE_HEALTH_DATA])).status_code, 405)

    def test_revoke_only_touches_own_records(self):
        self.grant(self.client_b)
        self.client.post(reverse('member_consent_revoke', args=[PURPOSE_HEALTH_DATA]))
        self.assertTrue(services.has_valid_consent(self.client_b, PURPOSE_HEALTH_DATA))

    def test_cannot_consent_on_behalf_of_someone_else(self):
        # No existe ninguna ruta con id de clienta; un campo "user" en el POST no se lee
        self.accept(user=str(self.client_b.pk), user_id=str(self.client_b.pk))
        self.assertEqual(list(ConsentRecord.objects.values_list('user', flat=True)), [self.client_a.pk])

    def test_next_is_followed_only_when_internal(self):
        url = reverse('member_consent', args=[PURPOSE_HEALTH_DATA])
        target = reverse('member_measurement_add')
        response = self.client.post(url, {'accept': 'on', 'text_version': '1', 'next': target})
        self.assertRedirects(response, target, fetch_redirect_response=False)
        ConsentRecord.objects.all().delete()
        response = self.client.post(url, {'accept': 'on', 'text_version': '1',
                                          'next': 'https://evil.example/robo'})
        self.assertRedirects(response, reverse('member_measurement_list'), fetch_redirect_response=False)

    def test_page_forwards_next_as_hidden_field(self):
        # Sin este campo oculto el navegador pierde el destino y la clienta cae en el historial.
        url = reverse('member_consent', args=[PURPOSE_HEALTH_DATA])
        target = reverse('member_health_profile')
        html = self.client.get(url, {'next': target}).content.decode()
        self.assertIn(f'<input type="hidden" name="next" value="{target}">', html)
        html = self.client.get(url, {'next': 'https://evil.example/robo'}).content.decode()
        self.assertNotIn('name="next"', html)

    @override_settings(PRIVACY_POLICY_URL='https://example.com/politica')
    def test_privacy_policy_url_is_passed_when_configured(self):
        response = self.client.get(reverse('member_consent', args=[PURPOSE_HEALTH_DATA]))
        self.assertEqual(response.context['privacy_policy_url'], 'https://example.com/politica')


class ConsentTextTests(HealthScenarioTestCase):
    def test_placeholder_detection(self):
        self.assertEqual(find_placeholders('a [NIT] b [NIT] c [RAZÓN SOCIAL]'), ['[NIT]', '[RAZÓN SOCIAL]'])
        self.assertEqual(find_placeholders('sin marcadores (nada)'), [])

    def test_ai_texts_not_ready_in_production_while_markers_remain(self):
        with override_settings(DEBUG=False):
            self.assertFalse(services.ai_texts_ready())
            self.assertEqual(set(services.purposes_with_placeholders()), {PURPOSE_HEALTH_DATA, PURPOSE_AI})
        with override_settings(DEBUG=True):
            self.assertTrue(services.ai_texts_ready())

    def test_ai_texts_ready_once_the_final_versions_are_published(self):
        self.publish_new_version(PURPOSE_HEALTH_DATA)
        self.publish_new_version(PURPOSE_AI)
        with override_settings(DEBUG=False):
            self.assertTrue(services.ai_texts_ready())

    def test_only_one_current_version_per_purpose(self):
        new = self.publish_new_version(PURPOSE_HEALTH_DATA)
        current = ConsentTextVersion.objects.filter(purpose=PURPOSE_HEALTH_DATA, is_current=True)
        self.assertEqual(list(current), [new])
        self.assertTrue(ConsentTextVersion.objects.get(purpose=PURPOSE_AI, version=1).is_current)

    def test_text_already_accepted_cannot_be_rewritten(self):
        self.grant(self.client_a)
        text = services.current_text(PURPOSE_HEALTH_DATA)
        text.body = 'otro texto'
        with self.assertRaises(ValidationError):
            text.save()
        text.refresh_from_db()
        self.assertIn('[NIT]', text.body)

    def test_accepted_text_cannot_be_deleted(self):
        from django.db.models import ProtectedError
        self.grant(self.client_a)
        with self.assertRaises(ProtectedError):
            services.current_text(PURPOSE_HEALTH_DATA).delete()

    def test_is_minor_uses_latest_assessment_age(self):
        self.assertFalse(services.is_minor(self.client_a))          # 30 años
        InitialAssessment.objects.filter(user=self.client_a).update(age=17)
        self.assertTrue(services.is_minor(self.client_a))
        InitialAssessment.objects.filter(user=self.client_a).update(age=18)
        self.assertFalse(services.is_minor(self.client_a))
        self.assertFalse(services.is_minor(self.new_member('sin_valoracion')))


class ConsentAdminTests(HealthScenarioTestCase):
    def test_only_the_boss_manages_consent_texts(self):
        url = reverse('admin:health_consenttextversion_changelist')
        self.login(self.boss)
        self.assertEqual(self.client.get(url).status_code, 200)
        for user in (self.trainer_a, self.client_a):
            self.login(user)
            response = self.client.get(url)
            self.assertIn(response.status_code, (302, 403), user.username)
            self.assertNotEqual(response.status_code, 200)

    def test_consent_records_and_texts_cannot_be_deleted_from_admin(self):
        self.login(self.boss)
        text = services.current_text(PURPOSE_HEALTH_DATA)
        response = self.client.post(
            reverse('admin:health_consenttextversion_delete', args=[text.pk]), {'post': 'yes'})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(ConsentTextVersion.objects.filter(pk=text.pk).exists())

    def test_consent_records_are_not_in_the_admin(self):
        self.assertFalse(admin.site.is_registered(ConsentRecord))
