"""Vistas de planes: autorización (matriz del contrato, sección 13), interruptores, no-store,
sondeo HTMX y formularios."""
from django.urls import reverse

from apps.plans import services
from apps.plans.models import Plan, PlanGenerationJob

from .helpers import PlanScenarioTestCase


class PlanViewsTestCase(PlanScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.ready(self.client_a)
        job, _ = self.request(self.client_a)
        self.job = job
        self.plan = job.plan


class TrainerAuthorizationTests(PlanViewsTestCase):
    def trainer_urls(self):
        pk, client_pk, job_pk = self.plan.pk, self.client_a.pk, self.job.pk
        return [
            ('get', reverse('trainer_plan_detail', args=[pk])),
            ('get', reverse('trainer_plan_job', args=[job_pk])),
            ('get', reverse('trainer_plan_job_poll', args=[job_pk])),
            ('post', reverse('trainer_plan_take', args=[pk])),
            ('post', reverse('trainer_plan_approve', args=[pk])),
            ('post', reverse('trainer_plan_regenerate', args=[pk])),
            ('post', reverse('trainer_plan_archive', args=[pk])),
            ('post', reverse('trainer_plan_generate', args=[client_pk])),
        ]

    def test_other_trainer_gets_404_everywhere(self):
        self.login(self.trainer_b)
        for method, url in self.trainer_urls():
            with self.subTest(url=url):
                self.assertEqual(getattr(self.client, method)(url).status_code, 404)

    def test_client_gets_403_and_anonymous_goes_to_login(self):
        self.login(self.client_a)
        for method, url in self.trainer_urls():
            with self.subTest(url=url):
                self.assertEqual(getattr(self.client, method)(url).status_code, 403)
        self.client.logout()
        for method, url in self.trainer_urls():
            with self.subTest(url=url):
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response['Location'])

    def test_assigned_trainer_and_superuser_see_the_plan(self):
        for user in (self.trainer_a, self.boss):
            self.login(user)
            response = self.client.get(reverse('trainer_plan_detail', args=[self.plan.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Cache-Control'].count('no-store'), 1)
            self.assertTrue(response.context['profile_summary'])

    def test_inbox_only_lists_own_clients(self):
        self.login(self.trainer_b)
        response = self.client.get(reverse('trainer_plan_inbox'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['rows'], [])
        self.login(self.trainer_a)
        response = self.client.get(reverse('trainer_plan_inbox'))
        self.assertEqual([row['plan'] for row in response.context['rows']], [self.plan])
        self.assertContains(response, 'MODO DE PRUEBA')


class TrainerActionTests(PlanViewsTestCase):
    def test_take_then_builder(self):
        self.login(self.trainer_a)
        response = self.client.post(reverse('trainer_plan_take', args=[self.plan.pk]), {'then': 'builder'})
        self.assertRedirects(response, reverse('trainer_routine_builder', args=[self.plan.routine_id]),
                             fetch_redirect_response=False)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, Plan.STATUS_IN_REVIEW)
        builder = self.client.get(reverse('trainer_routine_builder', args=[self.plan.routine_id]))
        self.assertContains(builder, 'borrador de plan con IA')

    def test_simulated_plan_approve_is_refused_in_production(self):
        self.login(self.trainer_a)
        response = self.client.post(reverse('trainer_plan_approve', args=[self.plan.pk]))
        self.assertEqual(response.status_code, 400)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, Plan.STATUS_DRAFT_AI)

    def test_approve_by_form(self):
        Plan.objects.filter(pk=self.plan.pk).update(is_simulated=False)   # como si viniera de Gemini
        self.login(self.trainer_a)
        response = self.client.post(reverse('trainer_plan_approve', args=[self.plan.pk]),
                                    {'note_for_client': 'Empieza suave'})
        self.assertRedirects(response, reverse('trainer_plan_detail', args=[self.plan.pk]),
                             fetch_redirect_response=False)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, Plan.STATUS_APPROVED)

    def test_note_with_html_is_rejected(self):
        self.login(self.trainer_a)
        response = self.client.post(reverse('trainer_plan_approve', args=[self.plan.pk]),
                                    {'note_for_client': '<script>x</script>'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('note_for_client', response.context['approve_errors'])

    def test_regenerate_and_archive_by_form(self):
        self.login(self.trainer_a)
        with self.fake_ai(), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('trainer_plan_regenerate', args=[self.plan.pk]),
                                        {'notes': 'Menos brazo', 'request_token': services.new_request_token()})
        job = PlanGenerationJob.objects.latest('pk')
        self.assertRedirects(response, reverse('trainer_plan_job', args=[job.pk]), fetch_redirect_response=False)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED)
        new_plan = job.plan
        response = self.client.post(reverse('trainer_plan_archive', args=[new_plan.pk]), {'reason': 'Prueba'})
        self.assertRedirects(response, reverse('trainer_plan_inbox'), fetch_redirect_response=False)
        new_plan.refresh_from_db()
        self.assertEqual(new_plan.status, Plan.STATUS_ARCHIVED)

    def test_generate_from_client_detail(self):
        self.login(self.trainer_a)
        detail = self.client.get(reverse('trainer_client_detail', args=[self.client_a.pk]))
        self.assertContains(detail, reverse('trainer_plan_generate', args=[self.client_a.pk]))
        with self.fake_ai(), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('trainer_plan_generate', args=[self.client_a.pk]),
                                        {'request_token': services.new_request_token()})
        job = PlanGenerationJob.objects.latest('pk')
        self.assertEqual(job.requested_by, self.trainer_a)
        self.assertRedirects(response, reverse('trainer_plan_job', args=[job.pk]), fetch_redirect_response=False)
        poll = self.client.get(reverse('trainer_plan_job_poll', args=[job.pk]))
        self.assertEqual(poll.status_code, 286)
        self.assertContains(poll, reverse('trainer_plan_detail', args=[job.plan_id]), status_code=286)


class MemberViewsTests(PlanViewsTestCase):
    def test_member_pages(self):
        self.login(self.client_a)
        response = self.client.get(reverse('member_plans'))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['exercise_plan'])
        self.assertTrue(response.context['pending'])
        self.assertNotContains(response, self.plan.routine.name)     # el borrador no se muestra
        self.assertIn('no-store', response['Cache-Control'])
        response = self.client.get(reverse('member_plan_request'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['eligibility']['can_request'])
        self.assertContains(response, 'No reemplaza la valoración')    # descargo

    def test_status_and_poll_only_for_own_job(self):
        self.login(self.client_a)
        response = self.client.get(reverse('member_plan_request_status', args=[self.job.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.plan.routine.name)
        poll = self.client.get(reverse('member_plan_request_poll', args=[self.job.pk]))
        self.assertEqual(poll.status_code, 286)
        self.login(self.client_b)
        self.assertEqual(self.client.get(reverse('member_plan_request_status', args=[self.job.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('member_plan_request_poll', args=[self.job.pk])).status_code, 404)

    def test_trainer_cannot_use_member_routes(self):
        self.login(self.trainer_a)
        for name in ('member_plans', 'member_plan_request'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_request_by_form_and_missing_requirements(self):
        self.login(self.client_b)          # sin consentimientos ni ficha
        response = self.client.get(reverse('member_plan_request'))
        codes = [item['code'] for item in response.context['eligibility']['missing']]
        self.assertIn('D01_CONSENT_AI', codes)
        self.assertTrue(all(item['url'] for item in response.context['eligibility']['missing']
                            if item['code'].startswith(('D01', 'D02', 'D03'))))
        response = self.client.post(reverse('member_plan_request'),
                                    {'kind': 'exercise', 'request_token': services.new_request_token()})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PlanGenerationJob.objects.filter(client=self.client_b).exists())
        self.ready(self.client_b)
        with self.fake_ai(), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('member_plan_request'),
                                        {'kind': 'exercise', 'request_token': services.new_request_token()})
        job = PlanGenerationJob.objects.get(client=self.client_b)
        self.assertRedirects(response, reverse('member_plan_request_status', args=[job.pk]),
                             fetch_redirect_response=False)

    def test_blocked_request_shows_referral_message(self):
        self.login(self.client_b)
        self.ready(self.client_b, pregnancy_status='pregnant')
        response = self.client.get(reverse('member_plan_request'))
        self.assertTrue(response.context['eligibility']['blocked'])
        self.assertNotContains(response, 'embarazo')
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('member_plan_request'),
                                        {'kind': 'exercise', 'request_token': services.new_request_token()})
        job = PlanGenerationJob.objects.get(client=self.client_b)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_BLOCKED)
        status = self.client.get(reverse('member_plan_request_status', args=[job.pk]))
        self.assertContains(status, 'Tu entrenador ya fue avisado')


class SwitchTests(PlanViewsTestCase):
    def test_everything_is_404_when_off(self):
        urls = [reverse('member_plans'), reverse('member_plan_request'), reverse('trainer_plan_inbox'),
                reverse('trainer_plan_detail', args=[self.plan.pk])]
        for settings in ({'AI_PLANS_MODE': 'off'}, {'HEALTH_FEATURES_ENABLED': False}):
            with self.settings(**settings):
                for user, url in ((self.client_a, urls[0]), (self.client_a, urls[1]),
                                  (self.trainer_a, urls[2]), (self.trainer_a, urls[3])):
                    self.login(user)
                    with self.subTest(settings=settings, url=url):
                        self.assertEqual(self.client.get(url).status_code, 404)

    def test_menu_links_follow_the_switch(self):
        self.login(self.trainer_a)
        self.assertContains(self.client.get(reverse('trainer_dashboard')), reverse('trainer_plan_inbox'))
        with self.settings(AI_PLANS_MODE='off'):
            self.assertNotContains(self.client.get(reverse('trainer_dashboard')), reverse('trainer_plan_inbox'))
