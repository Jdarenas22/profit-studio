"""Mediciones de la clienta (fase A1): acceso, consentimiento, validaciones, borrado y
autorización cruzada. Más la edición/borrado por el entrenador y el flujo existente."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import Client, override_settings
from django.urls import reverse
from django.utils.html import escape

from apps.assessments import services
from apps.assessments.forms import MemberMeasurementForm
from apps.assessments.models import BodyMeasurement, InitialAssessment
from apps.health.models import PURPOSE_HEALTH_DATA
from apps.health.tests.helpers import HealthScenarioTestCase, stub_missing_templates
from apps.memberships.models import Membership


def member_payload(**over):
    data = {'weight': '62,5', 'waist_cm': '70', 'hip_cm': '96,5', 'body_fat_pct': '27'}
    data.update(over)
    return data


@stub_missing_templates()
class MemberMeasurementAccessTests(HealthScenarioTestCase):
    def urls(self, pk=None):
        urls = [reverse('member_measurement_list'), reverse('member_measurement_add')]
        if pk:
            urls.append(reverse('member_measurement_delete', args=[pk]))
        return urls

    def test_anonymous_goes_to_login(self):
        pk = BodyMeasurement.objects.filter(user=self.client_a).first().pk
        for url in self.urls(pk):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn('/accounts/login/', response['Location'])
        response = self.client.post(self.urls(pk)[2])
        self.assertEqual(response.status_code, 302)
        self.assertTrue(BodyMeasurement.objects.filter(pk=pk).exists())

    def test_trainers_get_403(self):
        pk = BodyMeasurement.objects.filter(user=self.client_a).first().pk
        for user in (self.trainer_a, self.boss):
            self.login(user)
            for url in self.urls(pk):
                self.assertEqual(self.client.get(url).status_code, 403, (user.username, url))
            self.assertEqual(self.client.post(self.urls(pk)[1], member_payload()).status_code, 403)
            self.assertEqual(self.client.post(self.urls(pk)[2]).status_code, 403)
        self.assertTrue(BodyMeasurement.objects.filter(pk=pk).exists())

    def test_membership_is_required(self):
        user = self.new_member('sin_membresia', with_membership=False)
        self.login(user)
        self.assertTemplateUsed(self.client.get(self.urls()[0]), 'accounts/no_membership.html')
        Membership.objects.filter(user=self.client_a).update(end_date=date.today() - timedelta(days=1))
        self.login(self.client_a)
        for url in self.urls():
            self.assertTemplateUsed(self.client.get(url), 'accounts/membership_expired.html')
        self.grant(self.client_a)
        self.client.post(self.urls()[1], member_payload())
        self.assertFalse(BodyMeasurement.objects.filter(user=self.client_a, source='member').exists())

    def test_feature_flag_off_returns_404(self):
        self.login(self.client_a)
        with override_settings(HEALTH_FEATURES_ENABLED=False):
            for url in self.urls():
                self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_responses_are_no_store(self):
        self.login(self.client_a)
        self.grant(self.client_a)
        for url in self.urls():
            self.assertIn('no-store', self.client.get(url)['Cache-Control'], url)

    def test_minor_cannot_register_measurements(self):
        InitialAssessment.objects.filter(user=self.client_a).update(age=16)
        self.login(self.client_a)
        self.grant(self.client_a)
        self.assertEqual(self.client.get(self.urls()[1]).status_code, 403)
        self.assertEqual(self.client.post(self.urls()[1], member_payload()).status_code, 403)
        self.assertFalse(BodyMeasurement.objects.filter(user=self.client_a, source='member').exists())
        self.assertEqual(self.client.get(self.urls()[0]).status_code, 200)   # puede ver su historial


@stub_missing_templates()
class MemberMeasurementAddTests(HealthScenarioTestCase):
    def setUp(self):
        self.login(self.client_a)
        self.url = reverse('member_measurement_add')

    def members_rows(self, user=None):
        return BodyMeasurement.objects.filter(user=user or self.client_a, source='member')

    def test_without_consent_it_asks_for_it_first(self):
        response = self.client.get(self.url)
        expected = f"{reverse('member_consent', args=[PURPOSE_HEALTH_DATA])}?next={self.url}"
        self.assertRedirects(response, expected, fetch_redirect_response=False)
        response = self.client.post(self.url, member_payload())
        self.assertRedirects(response, expected, fetch_redirect_response=False)
        self.assertFalse(self.members_rows().exists())

    def test_revoked_consent_blocks_again(self):
        self.grant(self.client_a)
        self.client.post(reverse('member_consent_revoke', args=[PURPOSE_HEALTH_DATA]))
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_new_text_version_blocks_until_accepted(self):
        self.grant(self.client_a)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.publish_new_version(PURPOSE_HEALTH_DATA)
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_form_context_inherits_last_height(self):
        self.grant(self.client_a)
        ctx = self.client.get(self.url).context
        self.assertFalse(ctx['height_required'])
        self.assertEqual(ctx['last_height'], Decimal('1.65'))   # de la valoración inicial

    def test_valid_measurement_is_saved_as_member(self):
        self.grant(self.client_a)
        response = self.client.post(self.url, member_payload())
        self.assertRedirects(response, reverse('member_measurement_list'), fetch_redirect_response=False)
        m = self.members_rows().get()
        self.assertEqual(m.user, self.client_a)
        self.assertIsNone(m.trainer)
        self.assertEqual((m.weight, m.waist_cm, m.hip_cm, m.body_fat_pct),
                         (Decimal('62.5'), Decimal('70.0'), Decimal('96.5'), Decimal('27.0')))
        self.assertEqual(m.height, Decimal('1.65'))              # heredada
        self.assertEqual(m.imc, Decimal('22.96'))                # 62,5 / 1,65^2
        self.assertFalse(m.needs_review)

    def test_optional_fields_can_be_left_empty(self):
        self.grant(self.client_a)
        self.client.post(self.url, {'weight': '61'})
        m = self.members_rows().get()
        self.assertIsNone(m.waist_cm)
        self.assertIsNone(m.hip_cm)
        self.assertIsNone(m.body_fat_pct)

    def test_height_typed_in_cm_is_converted(self):
        self.grant(self.client_a)
        self.client.post(self.url, member_payload(height='170'))
        self.assertEqual(self.members_rows().get().height, Decimal('1.70'))

    def test_height_is_required_only_the_first_time(self):
        user = self.new_member('primera_vez')
        self.login(user)
        self.grant(user)
        ctx = self.client.get(self.url).context
        self.assertTrue(ctx['height_required'])
        self.assertIsNone(ctx['last_height'])
        response = self.client.post(self.url, {'weight': '65'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('height', response.context['errors'])
        self.assertFalse(self.members_rows(user).exists())
        self.client.post(self.url, {'weight': '65', 'height': '1,62'})
        self.assertEqual(self.members_rows(user).get().height, Decimal('1.62'))
        # la segunda vez ya no la pide
        self.assertFalse(self.client.get(self.url).context['height_required'])
        self.client.post(self.url, {'weight': '64'})
        self.assertEqual(self.members_rows(user).count(), 2)

    def test_height_falls_back_to_previous_measurement_before_assessment(self):
        self.grant(self.client_a)
        BodyMeasurement.objects.create(user=self.client_a, weight=60, height='1.70')
        self.assertEqual(services.latest_height(self.client_a), Decimal('1.70'))

    def assert_rejected(self, field, **over):
        response = self.client.post(self.url, member_payload(**over))
        self.assertEqual(response.status_code, 200, over)
        self.assertIn(field, response.context['errors'], over)
        self.assertFalse(self.members_rows().exists(), over)
        return response

    def test_validations(self):
        self.grant(self.client_a)
        for field, over in [
            ('weight', {'weight': ''}), ('weight', {'weight': 'abc'}), ('weight', {'weight': '19'}),
            ('weight', {'weight': '401'}), ('weight', {'weight': 'NaN'}), ('weight', {'weight': 'Infinity'}),
            ('waist_cm', {'waist_cm': '29'}), ('waist_cm', {'waist_cm': '301'}),
            ('hip_cm', {'hip_cm': '29,9'}), ('hip_cm', {'hip_cm': '301'}), ('hip_cm', {'hip_cm': 'x'}),
            ('body_fat_pct', {'body_fat_pct': '2,9'}), ('body_fat_pct', {'body_fat_pct': '70.1'}),
            ('body_fat_pct', {'body_fat_pct': 'mucha'}),
            ('height', {'height': '0.4'}), ('height', {'height': '3'}), ('height', {'height': '251'}),
            ('notes', {'notes': 'x' * 2001}),
        ]:
            self.assert_rejected(field, **over)

    def test_range_borders_are_accepted(self):
        self.grant(self.client_a)
        self.client.post(self.url, {'weight': '60', 'hip_cm': '30', 'body_fat_pct': '3'})
        self.client.post(self.url, {'weight': '60', 'hip_cm': '300', 'body_fat_pct': '70'})
        self.assertEqual(self.members_rows().count(), 2)

    def test_incoherent_imc_with_typed_height_blames_the_height(self):
        self.grant(self.client_a)
        self.assert_rejected('height', weight='300', height='1.65')

    def test_incoherent_imc_with_inherited_height_blames_the_weight(self):
        self.grant(self.client_a)
        self.assert_rejected('weight', weight='300')

    def test_errors_are_painted_next_to_the_field(self):
        self.grant(self.client_a)
        response = self.assert_rejected('hip_cm', hip_cm='5')
        self.assertContains(response, escape(response.context['errors']['hip_cm']))
        # el error de un campo pintado no se repite como aviso arriba
        self.assertFalse([str(m) for m in response.context['messages']])

    def test_form_repopulates_with_what_was_typed(self):
        self.grant(self.client_a)
        response = self.assert_rejected('hip_cm', hip_cm='5', weight='62,5')
        self.assertEqual(response.context['form']['weight'], '62,5')

    def test_daily_limit_is_three(self):
        self.grant(self.client_a)
        for _ in range(3):
            self.client.post(self.url, {'weight': '61'})
        self.assertEqual(self.members_rows().count(), 3)
        response = self.client.post(self.url, {'weight': '61'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('general', response.context['errors'])
        self.assertEqual(self.members_rows().count(), 3)
        # el límite es por clienta: la otra no se afecta
        self.login(self.client_b)
        self.grant(self.client_b)
        self.client.post(self.url, {'weight': '61'})
        self.assertEqual(self.members_rows(self.client_b).count(), 1)

    def test_measurements_of_the_trainer_do_not_use_the_daily_limit(self):
        self.grant(self.client_a)
        for _ in range(3):
            BodyMeasurement.objects.create(user=self.client_a, trainer=self.trainer_a, weight=60)
        self.client.post(self.url, {'weight': '61'})
        self.assertEqual(self.members_rows().count(), 1)

    def test_big_weight_jump_is_saved_but_flagged(self):
        self.grant(self.client_a)               # ya hay una medición de 60 kg de hoy
        response = self.client.post(self.url, {'weight': '66'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.members_rows().get().needs_review)

    def test_small_weight_change_is_not_flagged(self):
        self.grant(self.client_a)
        self.client.post(self.url, {'weight': '64,9'})
        self.assertFalse(self.members_rows().get().needs_review)

    def test_old_measurement_does_not_trigger_the_flag(self):
        self.grant(self.client_a)
        BodyMeasurement.objects.filter(user=self.client_a).update(date=date.today() - timedelta(days=15))
        self.client.post(self.url, {'weight': '80'})
        self.assertFalse(self.members_rows().get().needs_review)

    def test_no_health_data_in_logs(self):
        self.grant(self.client_a)
        with self.assertNoLogs(level='DEBUG'):
            self.client.post(self.url, member_payload(weight='71,3', notes='nota privada'))
        self.assertTrue(self.members_rows().exists())

    def test_post_requires_csrf_token(self):
        self.grant(self.client_a)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.client_a, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(strict.post(self.url, member_payload()).status_code, 403)
        self.assertFalse(self.members_rows().exists())


@stub_missing_templates()
class MemberMeasurementListTests(HealthScenarioTestCase):
    def setUp(self):
        self.login(self.client_a)
        self.url = reverse('member_measurement_list')

    def test_context_for_the_template(self):
        mine_member = BodyMeasurement.objects.create(
            user=self.client_a, weight=61, source='member', hip_cm='95', body_fat_pct='26')
        ctx = self.client.get(self.url).context
        self.assertTrue(ctx['needs_consent'])
        by_pk = {m.pk: m for m in ctx['measurements']}
        self.assertTrue(by_pk[mine_member.pk].can_delete)
        trainer_made = BodyMeasurement.objects.get(user=self.client_a, source='trainer')
        self.assertFalse(by_pk[trainer_made.pk].can_delete)
        self.assertEqual(by_pk[mine_member.pk].source, 'member')
        self.grant(self.client_a)
        self.assertFalse(self.client.get(self.url).context['needs_consent'])

    def test_most_recent_first(self):
        old = BodyMeasurement.objects.create(user=self.client_a, weight=59, source='member')
        BodyMeasurement.objects.filter(pk=old.pk).update(date=date.today() - timedelta(days=3))
        new = BodyMeasurement.objects.create(user=self.client_a, weight=58, source='member')
        pks = [m.pk for m in self.client.get(self.url).context['measurements']]
        self.assertLess(pks.index(new.pk), pks.index(old.pk))

    def test_only_own_measurements_are_listed(self):
        foreign = BodyMeasurement.objects.filter(user=self.client_b).first()
        ctx = self.client.get(self.url).context
        self.assertTrue(ctx['measurements'])
        self.assertNotIn(foreign.pk, [m.pk for m in ctx['measurements']])
        self.assertTrue(all(m.user_id == self.client_a.pk for m in ctx['measurements']))
        self.assertNotIn(foreign.pk, [m.pk for m in ctx['measurements']])

    def test_series_for_the_chart(self):
        series = self.client.get(self.url).context['series']
        self.assertEqual(set(series), {'dates', 'weight', 'waist_cm', 'hip_cm'})
        self.assertEqual(len(series['dates']), len(series['weight']))


@stub_missing_templates()
class MemberMeasurementDeleteTests(HealthScenarioTestCase):
    def setUp(self):
        self.login(self.client_a)
        self.mine = BodyMeasurement.objects.create(user=self.client_a, weight=61, source='member')

    def delete(self, pk, user_client=None):
        return (user_client or self.client).post(reverse('member_measurement_delete', args=[pk]))

    def test_can_delete_own_measurement_of_today(self):
        response = self.delete(self.mine.pk)
        self.assertRedirects(response, reverse('member_measurement_list'), fetch_redirect_response=False)
        self.assertFalse(BodyMeasurement.objects.filter(pk=self.mine.pk).exists())

    def test_delete_requires_post(self):
        response = self.client.get(reverse('member_measurement_delete', args=[self.mine.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(BodyMeasurement.objects.filter(pk=self.mine.pk).exists())

    def test_delete_requires_csrf_token(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.client_a, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.delete(self.mine.pk, strict).status_code, 403)
        self.assertTrue(BodyMeasurement.objects.filter(pk=self.mine.pk).exists())

    def test_cannot_delete_a_measurement_of_another_day(self):
        BodyMeasurement.objects.filter(pk=self.mine.pk).update(date=date.today() - timedelta(days=1))
        response = self.delete(self.mine.pk)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(BodyMeasurement.objects.filter(pk=self.mine.pk).exists())

    def test_cannot_delete_a_measurement_made_by_the_trainer(self):
        theirs = BodyMeasurement.objects.filter(user=self.client_a, source='trainer').first()
        self.delete(theirs.pk)
        self.assertTrue(BodyMeasurement.objects.filter(pk=theirs.pk).exists())

    def test_cannot_delete_another_clients_measurement(self):
        other = BodyMeasurement.objects.create(user=self.client_b, weight=70, source='member')
        self.assertEqual(self.delete(other.pk).status_code, 404)
        self.assertTrue(BodyMeasurement.objects.filter(pk=other.pk).exists())
        self.assertEqual(self.delete(999999).status_code, 404)

    def test_can_delete_helper(self):
        self.assertTrue(services.can_member_delete(self.mine, self.client_a))
        self.assertFalse(services.can_member_delete(self.mine, self.client_b))


@stub_missing_templates()
class TrainerMeasurementEditDeleteTests(HealthScenarioTestCase):
    def setUp(self):
        self.m_a = BodyMeasurement.objects.filter(user=self.client_a).first()
        self.m_b = BodyMeasurement.objects.filter(user=self.client_b).first()
        self.m_free = BodyMeasurement.objects.filter(user=self.client_free).first()

    def edit_url(self, m):
        return reverse('trainer_measurement_edit', args=[m.pk])

    def delete_url(self, m):
        return reverse('trainer_measurement_delete', args=[m.pk])

    def test_access_matrix_edit(self):
        # (usuario, medición, estado esperado de GET)
        cases = [
            (self.boss, self.m_a, 200), (self.boss, self.m_b, 200), (self.boss, self.m_free, 200),
            (self.trainer_a, self.m_a, 200), (self.trainer_a, self.m_b, 404),
            (self.trainer_a, self.m_free, 404), (self.trainer_b, self.m_a, 404),
            (self.trainer_b, self.m_b, 200), (self.client_a, self.m_a, 403), (self.client_a, self.m_b, 403),
        ]
        for user, m, expected in cases:
            self.login(user)
            self.assertEqual(self.client.get(self.edit_url(m)).status_code, expected, (user.username, m.pk))
            self.assertEqual(self.client.post(self.edit_url(m), {'weight': '70'}).status_code,
                             302 if expected == 200 else expected, (user.username, m.pk))

    def test_access_matrix_delete(self):
        cases = [
            (self.trainer_b, self.m_a, 404), (self.trainer_a, self.m_free, 404),
            (self.client_a, self.m_a, 403), (self.client_b, self.m_a, 403),
        ]
        for user, m, expected in cases:
            self.login(user)
            self.assertEqual(self.client.post(self.delete_url(m)).status_code, expected, (user.username, m.pk))
            self.assertTrue(BodyMeasurement.objects.filter(pk=m.pk).exists())

    def test_anonymous_goes_to_login(self):
        for url in (self.edit_url(self.m_a), self.delete_url(self.m_a)):
            response = self.client.post(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn('/accounts/login/', response['Location'])
        self.assertTrue(BodyMeasurement.objects.filter(pk=self.m_a.pk).exists())

    def test_trainer_deletes_own_clients_measurement_by_post_only(self):
        self.login(self.trainer_a)
        self.assertEqual(self.client.get(self.delete_url(self.m_a)).status_code, 405)
        self.assertTrue(BodyMeasurement.objects.filter(pk=self.m_a.pk).exists())
        response = self.client.post(self.delete_url(self.m_a))
        self.assertRedirects(response, reverse('trainer_client_detail', args=[self.client_a.pk]),
                             fetch_redirect_response=False)
        self.assertFalse(BodyMeasurement.objects.filter(pk=self.m_a.pk).exists())

    def test_trainer_can_delete_a_member_measurement_of_any_day(self):
        mine = BodyMeasurement.objects.create(user=self.client_a, weight=61, source='member')
        BodyMeasurement.objects.filter(pk=mine.pk).update(date=date.today() - timedelta(days=9))
        self.login(self.trainer_a)
        self.client.post(self.delete_url(mine))
        self.assertFalse(BodyMeasurement.objects.filter(pk=mine.pk).exists())

    def test_edit_updates_and_keeps_the_origin(self):
        mine = BodyMeasurement.objects.create(user=self.client_a, weight=61, height='1.65', source='member',
                                              needs_review=True)
        self.login(self.trainer_a)
        response = self.client.get(self.edit_url(mine))
        self.assertEqual(response.context['form']['weight'], '61.0')
        self.assertEqual(response.context['measurement'], mine)
        response = self.client.post(self.edit_url(mine), {
            'weight': '63,2', 'height': '1,65', 'hip_cm': '99', 'body_fat_pct': '25,5', 'notes': 'corregida'})
        self.assertRedirects(response, reverse('trainer_client_detail', args=[self.client_a.pk]),
                             fetch_redirect_response=False)
        mine.refresh_from_db()
        self.assertEqual((mine.weight, mine.hip_cm, mine.body_fat_pct, mine.notes),
                         (Decimal('63.2'), Decimal('99.0'), Decimal('25.5'), 'corregida'))
        self.assertEqual(mine.source, 'member')
        self.assertEqual(mine.user, self.client_a)
        self.assertFalse(mine.needs_review)
        self.assertEqual(mine.imc, Decimal('23.21'))

    def test_edit_rejects_invalid_data(self):
        self.login(self.trainer_a)
        for field, over in [('hip_cm', {'hip_cm': '2'}), ('body_fat_pct', {'body_fat_pct': '99'}),
                            ('weight', {'weight': '5'}), ('height', {'weight': '300', 'height': '1.65'})]:
            payload = {'weight': '70'}
            payload.update(over)
            response = self.client.post(self.edit_url(self.m_a), payload)
            self.assertEqual(response.status_code, 200, over)
            self.assertIn(field, response.context['errors'], over)
        self.m_a.refresh_from_db()
        self.assertEqual(self.m_a.weight, Decimal('60.0'))

    def test_edit_cannot_move_the_measurement_to_another_client(self):
        self.login(self.trainer_a)
        self.client.post(self.edit_url(self.m_a), {'weight': '70', 'user': str(self.client_b.pk)})
        self.m_a.refresh_from_db()
        self.assertEqual(self.m_a.user, self.client_a)

    def test_trainer_add_stores_new_fields_as_trainer(self):
        self.login(self.trainer_a)
        self.client.post(reverse('trainer_measurement_add', args=[self.client_a.pk]),
                         {'weight': '70', 'hip_cm': '101,5', 'body_fat_pct': '30'})
        m = BodyMeasurement.objects.filter(user=self.client_a).latest('pk')
        self.assertEqual((m.source, m.trainer, m.hip_cm, m.body_fat_pct),
                         ('trainer', self.trainer_a, Decimal('101.5'), Decimal('30.0')))

    def test_trainer_add_rejects_bad_hip_and_paints_the_error(self):
        self.login(self.trainer_a)
        count = BodyMeasurement.objects.count()
        response = self.client.post(reverse('trainer_measurement_add', args=[self.client_a.pk]),
                                    {'weight': '70', 'hip_cm': '2'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('hip_cm', response.context['errors'])
        self.assertEqual(BodyMeasurement.objects.count(), count)


class ServicesTests(HealthScenarioTestCase):
    def test_existing_rows_default_to_trainer_source(self):
        self.assertTrue(BodyMeasurement.objects.exists())
        self.assertFalse(BodyMeasurement.objects.exclude(source='trainer').exists())

    def test_latest_anthropometrics(self):
        data = services.latest_anthropometrics(self.client_a)
        self.assertEqual(data.weight, Decimal('60.0'))
        self.assertEqual(data.height, Decimal('1.65'))        # de la valoración inicial
        self.assertEqual(data.days_old, 0)
        old = BodyMeasurement.objects.create(user=self.client_a, weight=58, height='1.66')
        BodyMeasurement.objects.filter(pk=old.pk).update(date=date.today() - timedelta(days=40))
        data = services.latest_anthropometrics(self.client_a)
        self.assertEqual(data.weight, Decimal('60.0'))        # la más reciente por fecha
        BodyMeasurement.objects.filter(user=self.client_a).exclude(pk=old.pk).delete()
        data = services.latest_anthropometrics(self.client_a)
        self.assertEqual((data.weight, data.height, data.days_old), (Decimal('58.0'), Decimal('1.66'), 40))
        self.assertIsNone(services.latest_anthropometrics(self.new_member('sin_datos')))

    def test_member_form_requires_height_only_when_unknown(self):
        self.assertIn('height', MemberMeasurementForm({'weight': '60'}, known_height=None).errors)
        form = MemberMeasurementForm({'weight': '60'}, known_height=Decimal('1.60'))
        self.assertTrue(form.is_valid())
        self.assertEqual(form.effective_height, Decimal('1.60'))
        form = MemberMeasurementForm({'weight': '60', 'height': '170'}, known_height=Decimal('1.60'))
        self.assertTrue(form.is_valid())
        self.assertEqual(form.effective_height, Decimal('1.70'))
