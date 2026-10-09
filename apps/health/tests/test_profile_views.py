"""Rutas de la ficha de salud: autorización, flujos de la clienta y del entrenador, no-store."""
import json
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from django.contrib.messages import get_messages
from django.test import Client, override_settings
from django.urls import reverse

from apps.assessments.models import InitialAssessment
from apps.health import dates, services
from apps.health.models import PURPOSE_AI, PURPOSE_HEALTH_DATA, HealthAccessLog, HealthProfile
from apps.memberships.models import Membership

from .helpers import HealthScenarioTestCase, cleaned_profile, stub_missing_templates, valid_post

MEMBER_ROUTES = [
    ('GET', 'member_health_profile', []),
    ('POST', 'member_health_profile', []),
    ('POST', 'member_health_profile_confirm', []),
    ('GET', 'member_health_export', []),
    ('GET', 'member_health_delete', []),
    ('POST', 'member_health_delete', []),
]


def texts(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


class ViewTestCase(HealthScenarioTestCase):
    def call(self, method, name, args=None, data=None, **extra):
        url = reverse(name, args=args or [])
        return getattr(self.client, method.lower())(url, data or {}, **extra)

    def context(self, response, key):
        return response.context[key]


# ─── Autorización de las rutas de la clienta ──────────────────────────────────

@stub_missing_templates()
class MemberRouteAccessTests(ViewTestCase):
    def test_anonymous_goes_to_login(self):
        for method, name, args in MEMBER_ROUTES:
            response = self.call(method, name, args)
            self.assertEqual(response.status_code, 302, (method, name))
            self.assertIn('/accounts/login/', response['Location'])

    def test_trainers_and_boss_get_403_and_change_nothing(self):
        self.make_profile(self.client_a)
        for user in (self.trainer_a, self.boss):
            self.login(user)
            for method, name, args in MEMBER_ROUTES:
                data = valid_post() if (method, name) == ('POST', 'member_health_profile') else {}
                self.assertEqual(self.call(method, name, args, data).status_code, 403, (user.username, name))
        self.assertEqual(HealthProfile.objects.count(), 1)
        self.assertFalse(HealthAccessLog.objects.exists())

    def test_member_without_membership_is_stopped(self):
        user = self.new_member('sin_membresia', with_membership=False)
        self.login(user)
        for method, name, args in MEMBER_ROUTES:
            response = self.call(method, name, args)
            self.assertTemplateUsed(response, 'accounts/no_membership.html')
        self.assertFalse(HealthProfile.objects.exists())

    def test_expired_membership_cannot_even_delete_or_export(self):
        self.make_profile(self.client_a)
        Membership.objects.filter(user=self.client_a).update(end_date=date.today() - timedelta(days=1))
        self.login(self.client_a)
        for method, name, args in MEMBER_ROUTES:
            response = self.call(method, name, args)
            self.assertTemplateUsed(response, 'accounts/membership_expired.html')
        self.assertEqual(HealthProfile.objects.count(), 1)

    def test_feature_flag_off_hides_everything(self):
        self.make_profile(self.client_a)
        self.login(self.client_a)
        with override_settings(HEALTH_FEATURES_ENABLED=False):
            for method, name, args in MEMBER_ROUTES:
                self.assertEqual(self.call(method, name, args).status_code, 404, (method, name))
        self.assertEqual(HealthProfile.objects.count(), 1)

    def test_all_responses_are_no_store(self):
        self.make_profile(self.client_a)
        self.login(self.client_a)
        for name in ('member_health_profile', 'member_health_export', 'member_health_delete'):
            self.assertIn('no-store', self.client.get(reverse(name))['Cache-Control'], name)
        response = self.client.get(reverse('member_health_profile') + '?edit=1')
        self.assertIn('no-store', response['Cache-Control'])
        response = self.client.post(reverse('member_health_profile_confirm'))
        self.assertIn('no-store', response['Cache-Control'])
        response = self.client.post(reverse('member_health_profile'), valid_post())
        self.assertIn('no-store', response['Cache-Control'])

    def test_csrf_is_enforced(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.client_a, backend='django.contrib.auth.backends.ModelBackend')
        self.grant(self.client_a)
        for name in ('member_health_profile', 'member_health_profile_confirm', 'member_health_delete'):
            response = strict.post(reverse(name), valid_post() if name == 'member_health_profile' else {})
            self.assertEqual(response.status_code, 403, name)
        self.assertFalse(HealthProfile.objects.exists())

    def test_wrong_methods(self):
        self.login(self.client_a)
        self.assertEqual(self.client.get(reverse('member_health_profile_confirm')).status_code, 405)
        self.assertEqual(self.client.post(reverse('member_health_export')).status_code, 405)
        self.assertEqual(self.client.put(reverse('member_health_profile')).status_code, 405)
        self.assertEqual(self.client.delete(reverse('member_health_delete')).status_code, 405)


# ─── La clienta: llenar, editar, confirmar ────────────────────────────────────

@stub_missing_templates()
class MemberProfileFlowTests(ViewTestCase):
    def setUp(self):
        self.login(self.client_a)

    def test_get_without_consent_redirects_to_consent_with_next(self):
        response = self.client.get(reverse('member_health_profile'))
        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response['Location'])
        self.assertEqual(parsed.path, reverse('member_consent', args=['health_data']))
        self.assertEqual(parse_qs(parsed.query)['next'], [reverse('member_health_profile')])

    def test_get_with_consent_and_no_profile_shows_the_empty_form(self):
        self.grant(self.client_a)
        response = self.client.get(reverse('member_health_profile'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'health/profile_form.html')
        ctx = response.context
        self.assertEqual(ctx['mode'], 'member')
        self.assertIsNone(ctx['profile'])
        self.assertFalse(ctx['is_edit'])
        self.assertEqual(ctx['errors'], {})
        self.assertEqual(ctx['error_steps'], [])
        self.assertIsNone(ctx['first_error_step'])
        self.assertEqual([s['number'] for s in ctx['steps']], [1, 2, 3, 4, 5])
        self.assertEqual(ctx['form']['conditions'], [])
        self.assertEqual(ctx['form']['meal_slots'], ['breakfast', 'lunch', 'dinner'])
        self.assertEqual(len(ctx['parq_rows']), 7)
        self.assertEqual([r['required'] for r in ctx['meal_rows']], [True, False, True, False, True])
        self.assertEqual([r['code'] for r in ctx['meal_rows']],
                         ['breakfast', 'mid_morning', 'lunch', 'snack', 'dinner'])
        self.assertEqual(ctx['required_meal_slots'], ['breakfast', 'lunch', 'dinner'])
        self.assertIn('conditions', ctx['choices'])
        self.assertEqual(ctx['last_height'], Decimal('1.65'))
        self.assertLess(ctx['birth_date_min'], ctx['birth_date_max'])
        self.assertNotIn('flags', ctx)

    def test_valid_post_saves_and_redirects(self):
        self.grant(self.client_a)
        response = self.client.post(reverse('member_health_profile'), valid_post(
            conditions=['asthma', 'hypertension'], injuries=['knee'], training_goal='muscle_gain'))
        self.assertRedirects(response, reverse('member_health_profile'), fetch_redirect_response=False)
        profile = services.current_profile(self.client_a)
        self.assertEqual(profile.version, 1)
        self.assertEqual(profile.conditions, ['hypertension', 'asthma'])
        self.assertEqual(profile.injuries, ['knee'])
        self.assertIsNotNone(profile.confirmed_by_client_at)
        self.client_a.refresh_from_db()
        self.assertEqual(self.client_a.training_goal, 'muscle_gain')
        self.assertIn('Tu ficha quedó guardada.', texts(response))

    def test_ai_consent_is_not_required_to_save(self):
        self.grant(self.client_a)
        self.assertFalse(services.has_valid_consent(self.client_a, PURPOSE_AI))
        self.client.post(reverse('member_health_profile'), valid_post())
        self.assertTrue(HealthProfile.objects.filter(user=self.client_a).exists())

    def test_post_without_consent_saves_nothing(self):
        response = self.client.post(reverse('member_health_profile'), valid_post())
        self.assertEqual(response.status_code, 302)
        self.assertIn('/health/consent/health_data/', response['Location'])
        self.assertFalse(HealthProfile.objects.exists())

    def test_invalid_post_shows_errors_and_steps_and_keeps_values(self):
        self.grant(self.client_a)
        response = self.client.post(reverse('member_health_profile'), valid_post(
            conditions=['asthma', 'ibs'], parq_q2=None, session_minutes='5',
            meal_slots=['lunch'], liked_foods_text='<b>x</b>'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'health/profile_form.html')
        ctx = response.context
        self.assertEqual(set(ctx['errors']), {'parq_q2', 'session_minutes', 'meal_slots', 'liked_foods_text'})
        self.assertEqual(ctx['error_steps'], [3, 4, 5])
        self.assertEqual(ctx['first_error_step'], 3)
        self.assertEqual(ctx['form']['conditions'], ['asthma', 'ibs'])    # la lista completa, no el último
        self.assertEqual(ctx['form']['session_minutes'], '5')
        self.assertEqual(ctx['form']['meal_slots'], ['lunch'])
        self.assertTrue(all(isinstance(m, str) for m in ctx['errors'].values()))
        self.assertTrue(any('Revisa' in m for m in texts(response)))
        self.assertFalse(HealthProfile.objects.exists())

    def test_post_ignores_any_user_identifier(self):
        self.grant(self.client_a)
        self.grant(self.client_b)
        self.client.post(reverse('member_health_profile'), valid_post(user=self.client_b.pk, client_pk=self.client_b.pk,
                                                                     user_id=self.client_b.pk, is_current='on'))
        self.assertTrue(HealthProfile.objects.filter(user=self.client_a).exists())
        self.assertFalse(HealthProfile.objects.filter(user=self.client_b).exists())

    def test_minor_by_assessment_is_blocked_everywhere(self):
        InitialAssessment.objects.filter(user=self.client_a).update(age=15)
        self.grant(self.client_a)
        for method in ('get', 'post'):
            response = getattr(self.client, method)(reverse('member_health_profile'), valid_post() if method == 'post' else {})
            self.assertEqual(response.status_code, 403)
            self.assertTemplateUsed(response, 'health/minor_blocked.html')
        self.assertFalse(HealthProfile.objects.exists())

    def test_form_rejects_a_minor_birth_date(self):
        self.grant(self.client_a)
        response = self.client.post(reverse('member_health_profile'), valid_post(
            birth_date=(dates.today() - timedelta(days=365 * 16)).isoformat()))
        self.assertEqual(response.status_code, 200)
        self.assertIn('birth_date', response.context['errors'])
        self.assertEqual(response.context['error_steps'], [1])
        self.assertFalse(HealthProfile.objects.exists())

    def test_profile_birth_date_overrides_old_assessment_age(self):
        self.make_profile(self.client_a)
        InitialAssessment.objects.filter(user=self.client_a).update(age=15)
        self.assertEqual(self.client.get(reverse('member_health_profile')).status_code, 200)

    def test_detail_with_existing_profile(self):
        profile = self.make_profile(self.client_a)
        response = self.client.get(reverse('member_health_profile'))
        self.assertTemplateUsed(response, 'health/profile_detail.html')
        ctx = response.context
        self.assertEqual(ctx['profile'], profile)
        self.assertFalse(ctx['needs_confirmation'])
        self.assertFalse(ctx['edited_by_trainer'])
        self.assertTrue(ctx['health_consent_valid'])
        self.assertEqual({c['purpose'] for c in ctx['consent_cards']}, {'health_data', 'ai_processing'})
        self.assertEqual(len(ctx['versions']), 1)
        self.assertNotIn('flags', ctx)
        self.assertNotIn('R01', str(ctx['profile'].__dict__))

    def test_detail_after_trainer_correction_asks_for_confirmation(self):
        self.make_profile(self.client_a)
        services.save_profile(self.client_a, cleaned_profile(), actor=self.trainer_a)
        response = self.client.get(reverse('member_health_profile'))
        self.assertTrue(response.context['needs_confirmation'])
        self.assertTrue(response.context['edited_by_trainer'])
        response = self.client.post(reverse('member_health_profile_confirm'))
        self.assertRedirects(response, reverse('member_health_profile'), fetch_redirect_response=False)
        self.assertIsNotNone(services.current_profile(self.client_a).confirmed_by_client_at)
        response = self.client.get(reverse('member_health_profile'))
        self.assertFalse(response.context['needs_confirmation'])

    def test_edit_mode_prefills_the_form_from_the_profile(self):
        self.make_profile(self.client_a, conditions=['asthma'], injuries=['knee'], parq_q4='yes',
                          meal_slots=['breakfast', 'snack', 'lunch', 'dinner'], meal_time_snack='16:30',
                          training_goal='rehab')
        response = self.client.get(reverse('member_health_profile') + '?edit=1')
        self.assertTemplateUsed(response, 'health/profile_form.html')
        ctx = response.context
        self.assertTrue(ctx['is_edit'])
        self.assertEqual(ctx['form']['conditions'], ['asthma'])
        self.assertEqual(ctx['form']['injuries'], ['knee'])
        self.assertEqual(ctx['form']['parq_q4'], 'yes')
        self.assertEqual(ctx['form']['parq_q1'], 'no')
        self.assertEqual(ctx['form']['meal_time_snack'], '16:30')
        self.assertEqual(ctx['form']['training_goal'], 'rehab')
        snack = [r for r in ctx['meal_rows'] if r['code'] == 'snack'][0]
        self.assertTrue(snack['checked'])
        self.assertEqual(snack['time'], '16:30')
        self.assertEqual(ctx['parq_rows'][3]['value'], 'yes')

    def test_edit_mode_without_consent_goes_to_consent_but_detail_still_opens(self):
        self.make_profile(self.client_a)
        services.revoke_consent(self.client_a, PURPOSE_HEALTH_DATA)
        self.assertEqual(self.client.get(reverse('member_health_profile')).status_code, 200)   # derecho de acceso
        response = self.client.get(reverse('member_health_profile') + '?edit=1')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/health/consent/health_data/', response['Location'])

    def test_second_save_creates_version_two(self):
        self.grant(self.client_a)
        self.client.post(reverse('member_health_profile'), valid_post())
        self.client.post(reverse('member_health_profile'), valid_post(session_minutes='45'))
        self.assertEqual(list(HealthProfile.objects.filter(user=self.client_a).order_by('version')
                              .values_list('version', 'is_current', 'session_minutes')),
                         [(1, False, 60), (2, True, 45)])

    def test_client_a_never_sees_client_bs_profile(self):
        self.make_profile(self.client_b, other_condition_text='SECRETO-B', conditions=['other'])
        self.grant(self.client_a)
        response = self.client.get(reverse('member_health_profile') + '?edit=1')
        self.assertIsNone(response.context['profile'])
        self.assertNotIn('SECRETO-B', json.dumps(response.context['form'], default=str))
        # Su confirmación nunca toca la ficha de otra clienta
        services.save_profile(self.client_b, cleaned_profile(), actor=self.trainer_b)
        self.assertEqual(self.client.post(reverse('member_health_profile_confirm')).status_code, 302)
        self.assertIsNone(services.current_profile(self.client_b).confirmed_by_client_at)


@stub_missing_templates()
class MemberConfirmTests(ViewTestCase):
    def setUp(self):
        self.login(self.client_a)

    def test_confirm_without_profile(self):
        self.grant(self.client_a)
        response = self.client.post(reverse('member_health_profile_confirm'))
        self.assertRedirects(response, reverse('member_health_profile'), fetch_redirect_response=False)
        self.assertFalse(HealthProfile.objects.exists())

    def test_confirm_without_consent_asks_for_it(self):
        self.make_profile(self.client_a)
        services.revoke_consent(self.client_a, PURPOSE_HEALTH_DATA)
        response = self.client.post(reverse('member_health_profile_confirm'))
        self.assertIn('/health/consent/health_data/', response['Location'])


# ─── Exportar, borrar y revocar ───────────────────────────────────────────────

@stub_missing_templates()
class ExportDeleteRevokeTests(ViewTestCase):
    def setUp(self):
        self.login(self.client_a)

    def test_export_is_an_attachment_with_only_my_data(self):
        self.make_profile(self.client_a, other_condition_text='SECRETO-A', conditions=['other'])
        self.make_profile(self.client_b, other_condition_text='SECRETO-B', conditions=['other'])
        response = self.client.get(reverse('member_health_export'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('application/json', response['Content-Type'])
        self.assertIn('no-store', response['Cache-Control'])
        body = json.loads(response.content)
        self.assertEqual(body['health_profiles'][0]['other_condition_text'], 'SECRETO-A')
        self.assertNotIn('SECRETO-B', response.content.decode())
        self.assertEqual(HealthAccessLog.objects.filter(client=self.client_a, action='export').count(), 1)
        self.assertFalse(HealthAccessLog.objects.filter(client=self.client_b).exists())

    def test_export_works_even_without_current_consent(self):
        self.make_profile(self.client_a)
        services.revoke_consent(self.client_a, PURPOSE_HEALTH_DATA)
        self.assertEqual(self.client.get(reverse('member_health_export')).status_code, 200)

    def test_delete_page_and_post(self):
        self.make_profile(self.client_a)
        self.make_profile(self.client_a)
        self.make_profile(self.client_b)
        response = self.client.get(reverse('member_health_delete'))
        self.assertTemplateUsed(response, 'health/profile_delete.html')
        self.assertEqual(response.context['versions_count'], 2)
        self.assertTrue(response.context['has_profile'])
        self.assertFalse(response.context['from_revoke'])
        self.assertEqual(HealthProfile.objects.filter(user=self.client_a).count(), 2)   # GET no borra
        response = self.client.post(reverse('member_health_delete'))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(HealthProfile.objects.filter(user=self.client_a).exists())
        self.assertTrue(HealthProfile.objects.filter(user=self.client_b).exists())
        entry = HealthAccessLog.objects.get(client=self.client_a, action='delete')
        self.assertEqual(entry.actor, self.client_a)
        self.assertTrue(services.has_health_consent(self.client_a))

    def test_delete_without_profile(self):
        response = self.client.post(reverse('member_health_delete'))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(HealthAccessLog.objects.exists())

    def test_delete_cannot_touch_someone_elses_profile(self):
        self.make_profile(self.client_b)
        self.client.post(reverse('member_health_delete'), {'user': self.client_b.pk, 'client_pk': self.client_b.pk})
        self.assertTrue(HealthProfile.objects.filter(user=self.client_b).exists())

    def test_revoking_health_consent_offers_to_delete_the_profile(self):
        self.make_profile(self.client_a)
        response = self.client.post(reverse('member_consent_revoke', args=['health_data']))
        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response['Location'])
        self.assertEqual(parsed.path, reverse('member_health_delete'))
        self.assertEqual(parse_qs(parsed.query), {'from_revoke': ['1']})
        self.assertTrue(HealthProfile.objects.filter(user=self.client_a).exists())      # solo se ofrece
        page = self.client.get(response['Location'])
        self.assertTrue(page.context['from_revoke'])

    def test_revoking_without_profile_or_other_purpose_keeps_the_old_behavior(self):
        self.grant(self.client_a)
        response = self.client.post(reverse('member_consent_revoke', args=['health_data']))
        self.assertRedirects(response, reverse('member_measurement_list'), fetch_redirect_response=False)
        self.make_profile(self.client_a)
        self.grant(self.client_a, PURPOSE_AI)
        response = self.client.post(reverse('member_consent_revoke', args=['ai_processing']))
        self.assertRedirects(response, reverse('member_measurement_list'), fetch_redirect_response=False)


# ─── Entrenador ───────────────────────────────────────────────────────────────

TRAINER_ROUTES = [('GET', 'trainer_health_profile'), ('GET', 'trainer_health_profile_edit'),
                  ('POST', 'trainer_health_profile_edit')]


@stub_missing_templates()
class TrainerAccessTests(ViewTestCase):
    def url(self, name, client):
        return reverse(name, args=[client.pk])

    def request(self, method, name, client, data=None):
        return getattr(self.client, method.lower())(self.url(name, client), data or {})

    def test_anonymous_goes_to_login(self):
        for method, name in TRAINER_ROUTES:
            response = self.request(method, name, self.client_a)
            self.assertEqual(response.status_code, 302)
            self.assertIn('/accounts/login/', response['Location'])

    def test_clients_get_403(self):
        self.make_profile(self.client_a)
        self.login(self.client_a)
        for method, name in TRAINER_ROUTES:
            self.assertEqual(self.request(method, name, self.client_a, valid_post()).status_code, 403, name)
            self.assertEqual(self.request(method, name, self.client_b, valid_post()).status_code, 403, name)

    def test_matrix_of_trainers(self):
        self.make_profile(self.client_a)
        self.make_profile(self.client_free)
        cases = [
            (self.trainer_a, self.client_a, 200), (self.trainer_b, self.client_a, 404),
            (self.trainer_a, self.client_b, 404), (self.trainer_a, self.client_free, 404),
            (self.trainer_b, self.client_free, 404), (self.boss, self.client_a, 200),
            (self.boss, self.client_b, 200), (self.boss, self.client_free, 200),
        ]
        for trainer, client, expected in cases:
            self.login(trainer)
            who = (trainer.username, client.username)
            view = self.request('GET', 'trainer_health_profile', client)
            self.assertEqual(view.status_code, expected, who)
            edit = self.request('GET', 'trainer_health_profile_edit', client)
            if expected == 200:
                self.assertIn('no-store', view['Cache-Control'])
                self.assertIn('no-store', edit['Cache-Control'])
            if expected == 404:
                self.assertEqual(edit.status_code, 404, who)
                post = self.request('POST', 'trainer_health_profile_edit', client, valid_post())
                self.assertEqual(post.status_code, 404, who)
            else:
                self.assertEqual(edit.status_code, 200 if services.current_profile(client) else 302, who)

    def test_foreign_trainer_cannot_edit_or_log(self):
        self.make_profile(self.client_a)
        self.login(self.trainer_b)
        response = self.request('POST', 'trainer_health_profile_edit', self.client_a, valid_post(session_minutes='30'))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(HealthProfile.objects.filter(user=self.client_a).count(), 1)
        self.assertFalse(HealthAccessLog.objects.exists())

    def test_unknown_client_is_404(self):
        self.login(self.boss)
        self.assertEqual(self.client.get(reverse('trainer_health_profile', args=[999999])).status_code, 404)
        self.assertEqual(self.client.get(reverse('trainer_health_profile', args=[self.trainer_a.pk])).status_code, 404)

    def test_feature_flag_off_only_hides_the_edit_route(self):
        self.make_profile(self.client_a)
        self.login(self.trainer_a)
        with override_settings(HEALTH_FEATURES_ENABLED=False):
            self.assertEqual(self.request('GET', 'trainer_health_profile', self.client_a).status_code, 200)
            self.assertEqual(self.request('GET', 'trainer_health_profile_edit', self.client_a).status_code, 404)
            response = self.request('POST', 'trainer_health_profile_edit', self.client_a, valid_post())
            self.assertEqual(response.status_code, 404)
        self.assertEqual(HealthProfile.objects.filter(user=self.client_a).count(), 1)
        response = self.request('GET', 'trainer_health_profile', self.client_a)
        self.assertTrue(response.context['can_edit'])
        with override_settings(HEALTH_FEATURES_ENABLED=False):
            self.assertFalse(self.request('GET', 'trainer_health_profile', self.client_a).context['can_edit'])

    def test_csrf_is_enforced_on_edit(self):
        self.make_profile(self.client_a)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.trainer_a, backend='django.contrib.auth.backends.ModelBackend')
        response = strict.post(self.url('trainer_health_profile_edit', self.client_a), valid_post())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(HealthProfile.objects.count(), 1)


@stub_missing_templates()
class TrainerProfileViewTests(ViewTestCase):
    def setUp(self):
        self.login(self.trainer_a)

    def view(self, client=None):
        return self.client.get(reverse('trainer_health_profile', args=[(client or self.client_a).pk]))

    def test_shows_profile_flags_and_logs_the_view(self):
        profile = self.make_profile(self.client_a, conditions=['cancer_active'])
        response = self.view()
        self.assertTemplateUsed(response, 'trainer/health_profile.html')
        ctx = response.context
        self.assertEqual(ctx['profile'], profile)
        self.assertTrue(ctx['content_visible'])
        self.assertTrue(ctx['has_profile'])
        self.assertIn('R12_CANCER', [f.code for f in ctx['flags'].red])
        self.assertEqual(ctx['flags'].blocked_scopes, {'exercise', 'nutrition'})
        self.assertFalse(ctx['needs_confirmation'])
        self.assertIsNotNone(ctx['consent_health'])
        self.assertIsNone(ctx['consent_ai'])
        self.assertFalse(ctx['is_minor'])
        self.assertIsNotNone(ctx['anthropometrics'])
        entry = HealthAccessLog.objects.get(client=self.client_a)
        self.assertEqual((entry.actor, entry.action, entry.profile_version), (self.trainer_a, 'view', 1))

    def test_every_open_is_logged(self):
        self.make_profile(self.client_a)
        self.view()
        self.view()
        self.assertEqual(HealthAccessLog.objects.filter(client=self.client_a, action='view').count(), 2)

    def test_boss_is_logged_as_the_actor(self):
        self.make_profile(self.client_free)
        self.login(self.boss)
        self.view(self.client_free)
        self.assertEqual(HealthAccessLog.objects.get(client=self.client_free).actor, self.boss)

    def test_without_consent_the_content_is_hidden_and_nothing_is_logged(self):
        self.make_profile(self.client_a, other_condition_text='SECRETO-A', conditions=['other'])
        services.revoke_consent(self.client_a, PURPOSE_HEALTH_DATA)
        response = self.view()
        ctx = response.context
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ctx['has_profile'])
        self.assertIsNone(ctx['profile'])
        self.assertFalse(ctx['content_visible'])
        self.assertFalse(ctx['consent_valid'])
        self.assertIsNone(ctx['flags'])
        self.assertFalse(ctx['can_edit'])
        self.assertEqual(ctx['versions'], [])
        self.assertNotIn('SECRETO-A', response.content.decode())
        self.assertFalse(HealthAccessLog.objects.exists())

    def test_new_unaccepted_text_also_hides_the_content(self):
        self.make_profile(self.client_a)
        self.publish_new_version(PURPOSE_HEALTH_DATA)
        self.assertFalse(self.view().context['content_visible'])

    def test_client_without_profile(self):
        self.grant(self.client_a)
        response = self.view()
        ctx = response.context
        self.assertFalse(ctx['has_profile'])
        self.assertIsNone(ctx['profile'])
        self.assertIn('M04_NO_PROFILE', ctx['flags'].codes)
        self.assertFalse(HealthAccessLog.objects.exists())

    def test_unconfirmed_correction_is_flagged(self):
        self.make_profile(self.client_a)
        services.save_profile(self.client_a, cleaned_profile(), actor=self.trainer_a)
        ctx = self.view().context
        self.assertTrue(ctx['needs_confirmation'])
        self.assertIn('M04_PROFILE_UNCONFIRMED', ctx['flags'].codes)

    def test_minor_by_assessment_is_reported(self):
        InitialAssessment.objects.filter(user=self.client_a).update(age=16)
        self.assertTrue(self.view().context['is_minor'])


@stub_missing_templates()
class TrainerEditTests(ViewTestCase):
    def setUp(self):
        self.login(self.trainer_a)
        self.url = reverse('trainer_health_profile_edit', args=[self.client_a.pk])

    def test_get_shows_the_prefilled_form_and_logs_view(self):
        self.make_profile(self.client_a, injuries=['hip'], training_goal='cardio')
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, 'trainer/health_profile_form.html')
        ctx = response.context
        self.assertEqual(ctx['mode'], 'trainer')
        self.assertEqual(ctx['client'], self.client_a)
        self.assertEqual(ctx['form']['injuries'], ['hip'])
        self.assertEqual(ctx['form']['training_goal'], 'cardio')
        self.assertTrue(ctx['is_edit'])
        self.assertEqual(HealthAccessLog.objects.get(client=self.client_a).action, 'view')

    def test_valid_post_creates_an_unconfirmed_trainer_version(self):
        self.make_profile(self.client_a)
        response = self.client.post(self.url, valid_post(injuries=['knee'], training_goal='rehab'))
        self.assertRedirects(response, reverse('trainer_health_profile', args=[self.client_a.pk]),
                             fetch_redirect_response=False)
        profile = services.current_profile(self.client_a)
        self.assertEqual((profile.version, profile.created_by_role), (2, 'trainer'))
        self.assertEqual(profile.created_by, self.trainer_a)
        self.assertIsNone(profile.confirmed_by_client_at)
        self.assertEqual(profile.injuries, ['knee'])
        self.client_a.refresh_from_db()
        self.assertEqual(self.client_a.training_goal, 'rehab')
        self.assertEqual(HealthAccessLog.objects.filter(client=self.client_a, action='edit').count(), 1)

    def test_invalid_post_rerenders_with_errors_and_saves_nothing(self):
        self.make_profile(self.client_a)
        response = self.client.post(self.url, valid_post(
            birth_date=(dates.today() - timedelta(days=365 * 16)).isoformat(), parq_q7=None))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'trainer/health_profile_form.html')
        self.assertEqual(set(response.context['errors']), {'birth_date', 'parq_q7'})
        self.assertEqual(response.context['error_steps'], [1, 3])
        self.assertEqual(response.context['mode'], 'trainer')
        self.assertEqual(HealthProfile.objects.filter(user=self.client_a).count(), 1)
        self.assertFalse(HealthAccessLog.objects.filter(action='edit').exists())

    def test_trainer_cannot_create_a_profile(self):
        self.grant(self.client_a)
        for method in ('get', 'post'):
            response = getattr(self.client, method)(self.url, valid_post() if method == 'post' else {})
            self.assertRedirects(response, reverse('trainer_health_profile', args=[self.client_a.pk]),
                                 fetch_redirect_response=False)
        self.assertFalse(HealthProfile.objects.exists())
        self.assertFalse(HealthAccessLog.objects.exists())

    def test_without_the_clients_consent_nothing_can_be_edited(self):
        self.make_profile(self.client_a)
        services.revoke_consent(self.client_a, PURPOSE_HEALTH_DATA)
        for method in ('get', 'post'):
            response = getattr(self.client, method)(self.url, valid_post() if method == 'post' else {})
            self.assertEqual(response.status_code, 302)
        self.assertEqual(HealthProfile.objects.filter(user=self.client_a).count(), 1)
        self.assertFalse(HealthAccessLog.objects.exists())

    def test_boss_can_correct_an_unassigned_client(self):
        self.make_profile(self.client_free)
        self.login(self.boss)
        response = self.client.post(reverse('trainer_health_profile_edit', args=[self.client_free.pk]), valid_post())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(services.current_profile(self.client_free).created_by, self.boss)
