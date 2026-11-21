"""Camino guiado de la clienta hasta su plan con IA (docs/contracts/camino-guiado.md).

Lógica (`apps.plans.journey.client_journey`), `next` seguro en las pantallas a donde manda el camino,
panel y «Pedir mi plan», privacidad y consultas. Sin red: la IA es siempre `FakeAIClient`.
"""
import json
import re
from datetime import timedelta
from decimal import Decimal
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.assessments.models import BodyMeasurement, InitialAssessment
from apps.exercises.models import Exercise
from apps.health.models import PURPOSE_AI, PURPOSE_HEALTH_DATA
from apps.health.navigation import is_safe_next, safe_next, with_next
from apps.health.tests.helpers import valid_post
from apps.memberships.models import Membership
from apps.plans import journey as journey_module
from apps.plans import services
from apps.plans.journey import STEP_ORDER, client_journey
from apps.plans.models import KIND_EXERCISE, KIND_NUTRITION, Food, Plan, PlanGenerationJob
from apps.plans.rules import eligibility

from .helpers import NutritionScenarioTestCase

DASHBOARD = reverse('dashboard')
STEP_KEYS = {'code', 'title', 'description', 'status', 'url', 'cta', 'scope'}
PLAN_KEYS = {'state', 'url', 'cta', 'message'}
TOP_KEYS = {'steps', 'done_count', 'total', 'next_step', 'plans', 'all_ready', 'all_done'}
STATES = {'missing_steps', 'ready', 'generating', 'in_review', 'approved', 'blocked', 'quota'}
# Códigos internos que nunca deben llegar al contexto (requisitos, banderas, validaciones, faltantes)
TECH_CODES = re.compile(r'\b(?:D0\d|R\d\d|Y\d\d|M\d\d|V\d\d|I0\d)(?:_[A-Z_]+)?\b')


def statuses(journey):
    return [step['status'] for step in journey['steps']]


def step(journey, code):
    return next(item for item in journey['steps'] if item['code'] == code)


def next_of(url):
    """El `next` de una dirección (ya decodificado)."""
    return parse_qs(urlparse(url).query)['next'][0]


def strings_of(journey):
    """Todo el texto del camino que ve la clienta (sin los códigos de paso, que no se pintan)."""
    found = []
    for item in journey['steps']:
        found += [item['title'], item['description'], item['cta'] or '', item['url'] or '']
    for entry in journey['plans'].values():
        found += [entry['message'], entry['cta'] or '', entry['url'] or '']
    return found


class JourneyTestCase(NutritionScenarioTestCase):
    def journey(self, user=None, back_url=None):
        return client_journey(user or self.client_a, back_url)

    def drop_measures(self, user):
        BodyMeasurement.objects.filter(user=user).delete()

    def queue_job(self, client, kind=KIND_EXERCISE):
        """Un trabajo en cola (sin ejecutarlo: dentro de la prueba `on_commit` no se dispara)."""
        job, created = services.request_plan(client, kind, client, services.new_request_token())
        self.assertTrue(created)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_QUEUED)
        return job


# ─── Forma del contrato ───────────────────────────────────────────────────────

class ContractShapeTests(JourneyTestCase):
    def test_shape_is_exactly_the_contract(self):
        self.ready(self.client_a)
        result = self.journey()
        self.assertEqual(set(result), TOP_KEYS)
        self.assertEqual([item['code'] for item in result['steps']], list(STEP_ORDER))
        self.assertEqual([item['code'] for item in result['steps']],
                         ['consent_health', 'profile', 'measure', 'consent_ai', 'activity'])
        for item in result['steps']:
            self.assertEqual(set(item), STEP_KEYS)
            self.assertIn(item['status'], ('done', 'todo', 'locked'))
            self.assertIn(item['scope'], ('all', 'nutrition'))
            self.assertTrue(item['title'] and item['description'])
        self.assertEqual([item['scope'] for item in result['steps']], ['all', 'all', 'all', 'all', 'nutrition'])
        self.assertEqual(result['total'], 5)
        self.assertEqual(set(result['plans']), {'exercise', 'nutrition'})
        for entry in result['plans'].values():
            self.assertEqual(set(entry), PLAN_KEYS)
            self.assertIn(entry['state'], STATES)
            self.assertTrue(entry['message'])

    def test_next_step_is_the_first_todo_and_the_same_object(self):
        self.grant(self.client_b)
        result = self.journey(self.client_b)
        self.assertEqual(result['next_step']['code'], 'profile')
        self.assertIs(result['next_step'], step(result, 'profile'))

    def test_dependencies_always_point_to_an_earlier_step(self):
        for code, requires in journey_module._REQUIRES.items():
            self.assertLess(STEP_ORDER.index(requires), STEP_ORDER.index(code))


# ─── Pasos ────────────────────────────────────────────────────────────────────

class StepTests(JourneyTestCase):
    def test_without_consent_everything_is_locked_but_the_first_step(self):
        self.drop_measures(self.client_b)
        result = self.journey(self.client_b)
        self.assertEqual(statuses(result), ['todo', 'locked', 'locked', 'locked', 'locked'])
        self.assertEqual((result['done_count'], result['total']), (0, 5))
        self.assertEqual(result['next_step']['code'], 'consent_health')
        self.assertFalse(result['all_ready'])
        self.assertFalse(result['all_done'])
        first = step(result, 'consent_health')
        self.assertEqual(urlparse(first['url']).path, reverse('member_consent', args=[PURPOSE_HEALTH_DATA]))
        self.assertEqual(first['cta'], 'Autorizar')
        for code in ('profile', 'measure', 'consent_ai', 'activity'):
            self.assertIsNone(step(result, code)['url'], code)
            self.assertIsNone(step(result, code)['cta'], code)
        for entry in result['plans'].values():
            self.assertEqual(entry['state'], 'missing_steps')
            self.assertEqual(entry['url'], first['url'])
            self.assertEqual(entry['cta'], 'Autorizar')

    def test_with_the_health_consent_the_next_steps_open_but_activity_waits_for_the_profile(self):
        self.drop_measures(self.client_b)
        self.grant(self.client_b)
        result = self.journey(self.client_b)
        self.assertEqual(statuses(result), ['done', 'todo', 'todo', 'todo', 'locked'])
        self.assertEqual(result['next_step']['code'], 'profile')
        self.assertEqual(result['done_count'], 1)
        # Un paso hecho se puede revisar (sin `next`) y no tiene botón
        done = step(result, 'consent_health')
        self.assertEqual(done['url'], reverse('member_consent', args=[PURPOSE_HEALTH_DATA]))
        self.assertIsNone(done['cta'])
        # Sin ficha no se da por «hecho» el nivel de actividad
        self.assertNotEqual(step(result, 'activity')['status'], 'done')

    def test_profile_waiting_for_the_client_to_confirm_the_trainer_correction(self):
        self.ready(self.client_a)
        self.make_profile(self.client_a, actor=self.trainer_a)     # corrección del entrenador: sin confirmar
        result = self.journey()
        self.assertEqual(statuses(result), ['done', 'todo', 'done', 'done', 'locked'])
        profile = step(result, 'profile')
        self.assertEqual(profile['description'], eligibility.REQUIREMENT_TEXTS[eligibility.D02_PROFILE_CONFIRM])
        self.assertEqual(profile['cta'], 'Revisar y confirmar')
        self.assertIn('Confirma', profile['title'])
        self.assertEqual(urlparse(profile['url']).path, reverse('member_health_profile'))

    def test_counterproof_confirmed_profile_is_done(self):
        self.ready(self.client_a)
        self.assertEqual(step(self.journey(), 'profile')['status'], 'done')

    def test_old_measure_is_the_only_pending_step(self):
        self.ready(self.client_a)
        BodyMeasurement.objects.filter(user=self.client_a).update(date=timezone.localdate() - timedelta(days=45))
        result = self.journey()
        self.assertEqual(statuses(result), ['done', 'done', 'todo', 'done', 'done'])
        measure = step(result, 'measure')
        self.assertEqual(measure['description'], eligibility.REQUIREMENT_TEXTS[eligibility.D03_MEASURE])
        self.assertEqual(urlparse(measure['url']).path, reverse('member_measurement_add'))
        self.assertEqual(next_of(measure['url']), DASHBOARD)
        for entry in self.journey()['plans'].values():
            self.assertEqual((entry['state'], entry['url'], entry['cta']),
                             ('missing_steps', measure['url'], 'Registrar mis medidas'))

    def test_counterproof_recent_measure_is_done(self):
        self.ready(self.client_a)
        self.assertEqual(step(self.journey(), 'measure')['status'], 'done')

    def test_incoherent_data_asks_to_review_the_profile(self):
        self.ready(self.client_a)
        BodyMeasurement.objects.filter(user=self.client_a).update(weight=Decimal('400'))   # IMC fuera de rango
        measure = step(self.journey(), 'measure')
        self.assertEqual(measure['status'], 'todo')
        self.assertEqual(measure['description'], eligibility.REQUIREMENT_TEXTS[eligibility.D04_INCOHERENT])
        self.assertEqual(measure['cta'], 'Revisar mis datos')
        parsed = urlparse(measure['url'])
        self.assertEqual(parsed.path, reverse('member_health_profile'))
        self.assertEqual(parse_qs(parsed.query)['edit'], ['1'])
        self.assertEqual(next_of(measure['url']), DASHBOARD)

    def test_missing_ai_consent_is_its_own_step(self):
        self.ready(self.client_a)
        from apps.health import services as health
        health.revoke_consent(self.client_a, PURPOSE_AI)
        result = self.journey()
        self.assertEqual(statuses(result), ['done', 'done', 'done', 'todo', 'done'])
        ai_step = step(result, 'consent_ai')
        self.assertEqual(urlparse(ai_step['url']).path, reverse('member_consent', args=[PURPOSE_AI]))
        self.assertEqual(result['plans']['exercise']['state'], 'missing_steps')

    def test_without_activity_only_nutrition_is_blocked_by_steps(self):
        self.ready(self.client_a, basic=True)
        result = self.journey()
        self.assertEqual(statuses(result), ['done', 'done', 'done', 'done', 'todo'])
        self.assertEqual((result['done_count'], result['total']), (4, 5))
        self.assertTrue(result['all_ready'])          # ningún paso de alcance «all» pendiente
        self.assertFalse(result['all_done'])
        activity = step(result, 'activity')
        self.assertEqual(activity['scope'], 'nutrition')
        self.assertEqual(activity['cta'], 'Completar más detalles')
        self.assertTrue(activity['url'].endswith('#mas-detalles'))
        parsed = urlparse(activity['url'])
        self.assertEqual(parsed.path, reverse('member_health_profile'))
        self.assertEqual(parse_qs(parsed.query)['edit'], ['1'])
        self.assertEqual(next_of(activity['url']), DASHBOARD)
        self.assertEqual(result['next_step']['code'], 'activity')
        exercise, nutrition = result['plans']['exercise'], result['plans']['nutrition']
        self.assertEqual(exercise['state'], 'ready')
        self.assertEqual(nutrition['state'], 'missing_steps')
        self.assertEqual(nutrition['message'], eligibility.REQUIREMENT_TEXTS[eligibility.D07_NUTRITION_DETAILS])
        self.assertEqual((nutrition['url'], nutrition['cta']), (activity['url'], 'Completar más detalles'))

    def test_everything_done(self):
        self.ready(self.client_a)
        result = self.journey()
        self.assertEqual(statuses(result), ['done'] * 5)
        self.assertEqual((result['done_count'], result['total']), (5, 5))
        self.assertIsNone(result['next_step'])
        self.assertTrue(result['all_ready'])
        self.assertTrue(result['all_done'])

    def test_a_done_step_stays_done_when_the_consent_is_withdrawn_later(self):
        self.ready(self.client_a)
        from apps.health import services as health
        health.revoke_consent(self.client_a, PURPOSE_HEALTH_DATA)
        result = self.journey()
        self.assertEqual(step(result, 'consent_health')['status'], 'todo')
        self.assertEqual(step(result, 'profile')['status'], 'done')
        self.assertEqual(result['next_step']['code'], 'consent_health')
        self.assertFalse(result['all_ready'])

    def test_back_url_goes_to_every_pending_step_and_defaults_to_the_panel(self):
        self.drop_measures(self.client_b)
        self.grant(self.client_b)
        back = '/plans/request/?kind=nutrition'
        for given, expected in ((back, back), (None, DASHBOARD), ('https://evil.example/x', DASHBOARD),
                                ('//evil.example/x', DASHBOARD)):
            with self.subTest(back=given):
                result = self.journey(self.client_b, given)
                todo = [item for item in result['steps'] if item['status'] == 'todo']
                self.assertTrue(todo)
                for item in todo:
                    self.assertEqual(next_of(item['url']), expected, item['code'])
        self.assertIn('next=/plans/request/%3Fkind%3Dnutrition', self.journey(self.client_b, back)['steps'][1]['url'])


# ─── Estado de los planes ─────────────────────────────────────────────────────

class PlanStateTests(JourneyTestCase):
    def test_ready(self):
        self.ready(self.client_a)
        plans = self.journey()['plans']
        self.assertEqual(plans['exercise'], {
            'state': 'ready', 'url': reverse('member_plan_request') + '?kind=exercise',
            'cta': 'Pedir mi plan de ejercicio',
            'message': 'Ya puedes pedir tu plan de ejercicio. Tu entrenador lo revisa antes de que lo veas.'})
        self.assertEqual(plans['nutrition']['state'], 'ready')
        self.assertEqual(plans['nutrition']['url'], reverse('member_plan_request') + '?kind=nutrition')
        self.assertEqual(plans['nutrition']['cta'], 'Pedir mi plan de alimentación')

    def test_generating(self):
        self.ready(self.client_a)
        job = self.queue_job(self.client_a)
        plans = self.journey()['plans']
        self.assertEqual(plans['exercise']['state'], 'generating')
        self.assertEqual(plans['exercise']['url'], reverse('member_plan_request_status', args=[job.pk]))
        self.assertEqual(plans['exercise']['cta'], 'Ver cómo va')
        self.assertEqual(plans['nutrition']['state'], 'ready')        # el otro tipo no se mezcla
        # La clienta puede abrir esa dirección
        self.login(self.client_a)
        self.assertEqual(self.client.get(plans['exercise']['url']).status_code, 200)

    def test_generating_nutrition(self):
        self.ready(self.client_a)
        job = self.queue_job(self.client_a, KIND_NUTRITION)
        plans = self.journey()['plans']
        self.assertEqual(plans['nutrition']['state'], 'generating')
        self.assertEqual(plans['nutrition']['url'], reverse('member_plan_request_status', args=[job.pk]))
        self.assertEqual(plans['exercise']['state'], 'ready')

    def test_in_review_when_a_draft_waits_for_the_trainer(self):
        self.ready(self.client_a)
        job, _ = self.request(self.client_a)
        self.assertEqual(job.plan.status, Plan.STATUS_DRAFT_AI)
        entry = self.journey()['plans']['exercise']
        self.assertEqual((entry['state'], entry['url'], entry['cta']), ('in_review', None, None))
        self.assertIn('revisando', entry['message'])
        self.assertEqual(self.journey()['plans']['nutrition']['state'], 'ready')

    def test_in_review_also_while_the_trainer_has_taken_it(self):
        self.ready(self.client_a)
        job, _ = self.request(self.client_a)
        services.take_for_review(job.plan, self.trainer_a)
        job.plan.refresh_from_db()
        self.assertEqual(job.plan.status, Plan.STATUS_IN_REVIEW)
        self.assertEqual(self.journey()['plans']['exercise']['state'], 'in_review')

    def test_approved_exercise(self):
        self.ready(self.client_a)
        self.make_exercise(self.client_a, approve=True)
        entry = self.journey()['plans']['exercise']
        self.assertEqual((entry['state'], entry['url'], entry['cta']), ('approved', reverse('member_plans'), 'Ver mi plan'))
        self.assertIn('aprobado', entry['message'])

    def test_approved_nutrition_links_to_the_nutrition_screen(self):
        self.approved_nutrition(self.client_a)
        entry = self.journey()['plans']['nutrition']
        self.assertEqual((entry['state'], entry['url']), ('approved', reverse('member_nutrition_plan')))
        self.assertEqual(self.journey()['plans']['exercise']['state'], 'ready')
        self.login(self.client_a)
        self.assertEqual(self.client.get(entry['url']).status_code, 200)     # no la manda a otra parte

    def test_approved_wins_over_a_new_draft_and_over_a_running_job(self):
        self.ready(self.client_a)
        self.make_exercise(self.client_a, approve=True)
        self.request(self.client_a, actor=self.boss)                          # borrador nuevo
        self.assertEqual(self.journey()['plans']['exercise']['state'], 'approved')
        self.queue_job(self.client_a)                                         # y un pedido en curso
        self.assertEqual(self.journey()['plans']['exercise']['state'], 'approved')

    def test_nutrition_outside_the_wellness_scope_is_not_shown_as_approved(self):
        self.approved_nutrition(self.client_a)
        self.make_profile(self.client_a, conditions=['asthma'])               # ahora fuera del alcance
        from apps.plans import nutrition
        self.assertIsNone(nutrition.member_plan(self.client_a))
        entry = self.journey()['plans']['nutrition']
        self.assertEqual(entry['state'], 'blocked')
        self.assertEqual(entry['message'], 'Los menús de bienestar no cubren esta solicitud. La alimentación clínica está pausada.')

    def test_blocked_by_a_red_flag_has_a_generic_message_and_no_codes(self):
        self.ready(self.client_a, pregnancy_status='pregnant')
        result = self.journey()
        for entry in result['plans'].values():
            self.assertEqual((entry['state'], entry['url'], entry['cta']), ('blocked', None, None))
        self.assertEqual(result['plans']['exercise']['message'], journey_module.MESSAGE_BLOCKED)
        self.assertEqual(
            result['plans']['exercise']['message'],
            'Por la información de salud que registraste, lo más seguro es que tu médico o nutricionista te '
            'valore antes de armar un plan. Habla con tu entrenador.')
        # Alimentación: fuera del alcance de bienestar (el mismo texto que ya ve en «Pedir mi plan»)
        self.assertEqual(result['plans']['nutrition']['message'],
                         'Los menús de bienestar no cubren esta solicitud. La alimentación clínica está pausada.')
        self.assertEqual(statuses(result), ['done'] * 5)
        dump = json.dumps(result, ensure_ascii=False).lower()
        for secret in ('r02', 'pregnan', 'embarazo', 'red_codes', 'flags'):
            self.assertNotIn(secret, dump)

    def assertNoAvisadoClaim(self, text):
        lowered = text.lower()
        for claim in ('ya fue avisado', 'fue avisado', 'ya avisamos', 'te contactará'):
            self.assertNotIn(claim, lowered)

    def test_blocked_messages_never_claim_the_trainer_was_already_notified(self):
        """Antes de pedir nada no hay trabajo ni aviso: el panel no puede decir que «ya fue avisado»."""
        messages = {}
        self.ready(self.client_a, pregnancy_status='pregnant')                    # bandera roja (los dos)
        plans = self.journey()['plans']
        messages['generico_ejercicio'] = plans['exercise']['message']
        messages['fuera_de_alcance'] = plans['nutrition']['message']              # embarazo: bienestar no lo cubre
        self.ready(self.client_a)                                                 # ficha sin banderas
        Exercise.objects.update(is_active=False)
        Food.objects.update(is_active=False)
        plans = self.journey()['plans']
        self.assertEqual((plans['exercise']['state'], plans['nutrition']['state']), ('blocked', 'blocked'))
        messages['pocos_ejercicios'] = plans['exercise']['message']
        messages['pocos_alimentos'] = plans['nutrition']['message']
        self.assertEqual(messages['generico_ejercicio'], journey_module.MESSAGE_BLOCKED)
        self.assertEqual(messages['fuera_de_alcance'],
                         'Los menús de bienestar no cubren esta solicitud. La alimentación clínica está pausada.')
        self.assertEqual(messages['pocos_ejercicios'], journey_module._BLOCKED_FEW_ITEMS[eligibility.D06_FEW_EXERCISES][0])
        self.assertEqual(messages['pocos_alimentos'], journey_module._BLOCKED_FEW_ITEMS[eligibility.D06_FEW_FOODS][0])
        self.assertEqual(len(set(messages.values())), 4)
        for name, text in messages.items():
            with self.subTest(case=name):
                self.assertNoAvisadoClaim(text)
                if name != 'fuera_de_alcance':
                    self.assertIn('entrenador', text.lower())       # les dice con quién hablar

    def test_the_job_status_screen_keeps_the_original_blocked_message(self):
        """Contraprueba: ahí sí se avisó al entrenador, y el texto original no cambia."""
        self.ready(self.client_a, pregnancy_status='pregnant')
        self.assertIn('ya fue avisado', eligibility.BLOCKED_MESSAGE)
        job, _ = self.request(self.client_a)                                      # ella pide: queda bloqueado y se avisa
        self.assertEqual(job.status, PlanGenerationJob.STATUS_BLOCKED)
        self.login(self.client_a)
        response = self.client.get(reverse('member_plan_request_status', args=[job.pk]))
        self.assertIn('ya fue avisado', response.context['job_message'])
        # ... y el panel sigue sin afirmarlo
        entry = self.journey()['plans']['exercise']
        self.assertEqual((entry['state'], entry['message']), ('blocked', journey_module.MESSAGE_BLOCKED))

    def test_the_dashboard_blocked_message_does_not_claim_a_notice(self):
        self.ready(self.client_a, pregnancy_status='pregnant')
        self.login(self.client_a)
        response = self.client.get(reverse('dashboard'))
        for entry in response.context['journey']['plans'].values():
            self.assertEqual(entry['state'], 'blocked')
            self.assertNoAvisadoClaim(entry['message'])

    def test_blocked_only_for_the_kind_that_has_the_flag(self):
        self.ready(self.client_a, eating_disorder_history='yes')
        plans = self.journey()['plans']
        self.assertEqual(plans['exercise']['state'], 'ready')
        self.assertEqual(plans['nutrition']['state'], 'blocked')
        self.assertNotIn('trastorno', json.dumps(plans, ensure_ascii=False).lower())

    @override_settings(AI_PLANS_CLIENT_WEEKLY_LIMIT=1)
    def test_quota(self):
        self.ready(self.client_a)
        job, _ = self.request(self.client_a, scenario='permanent')
        self.assertEqual(job.status, PlanGenerationJob.STATUS_FAILED)
        result = self.journey()
        self.assertEqual(statuses(result), ['done'] * 5)
        for entry in result['plans'].values():
            self.assertEqual((entry['state'], entry['url'], entry['cta']), ('quota', None, None))
            self.assertEqual(entry['message'], eligibility.REQUIREMENT_TEXTS[eligibility.D05_QUOTA])

    @override_settings(AI_PLANS_CLIENT_WEEKLY_LIMIT=1)
    def test_missing_steps_come_before_the_quota(self):
        self.ready(self.client_a)
        self.request(self.client_a, scenario='permanent')
        self.drop_measures(self.client_a)
        plans = self.journey()['plans']
        self.assertEqual({entry['state'] for entry in plans.values()}, {'missing_steps'})

    def test_trainer_requests_do_not_consume_the_client_quota_shown(self):
        self.ready(self.client_a)
        for _ in range(4):
            self.request(self.client_a, actor=self.boss)
        # Hay borradores (en revisión), no cupo agotado de la clienta
        self.assertEqual(self.journey()['plans']['exercise']['state'], 'in_review')
        self.assertEqual(self.journey()['plans']['nutrition']['state'], 'ready')


# ─── Cuándo no aplica ─────────────────────────────────────────────────────────

class NotApplicableTests(JourneyTestCase):
    def setUp(self):
        super().setUp()
        self.ready(self.client_a)

    def test_baseline_applies(self):
        self.assertIsNotNone(self.journey())

    def test_feature_off(self):
        with override_settings(HEALTH_FEATURES_ENABLED=False):
            self.assertIsNone(self.journey())
        with override_settings(AI_PLANS_MODE='off'):
            self.assertIsNone(self.journey())

    def test_legal_texts_with_placeholders(self):
        with override_settings(DEBUG=False):
            self.publish_new_version(PURPOSE_AI, body='Texto con [RAZÓN SOCIAL] sin completar.')
            self.assertFalse(services.feature_available())
            self.assertIsNone(self.journey())

    def test_trainer_and_superuser(self):
        self.assertIsNone(self.journey(self.trainer_a))
        self.assertIsNone(self.journey(self.boss))

    def test_anonymous_and_none(self):
        self.assertIsNone(client_journey(AnonymousUser()))
        self.assertIsNone(client_journey(None))

    def test_without_membership(self):
        user = self.new_member('sin_membresia', with_membership=False)
        self.assertIsNone(client_journey(user))

    def fresh(self, user):
        return User.objects.get(pk=user.pk)

    def test_expired_or_inactive_membership(self):
        Membership.objects.filter(user=self.client_a).update(end_date=timezone.localdate() - timedelta(days=1))
        self.assertIsNone(client_journey(self.fresh(self.client_a)))
        Membership.objects.filter(user=self.client_a).update(
            end_date=timezone.localdate() + timedelta(days=30), is_active=False)
        self.assertIsNone(client_journey(self.fresh(self.client_a)))
        Membership.objects.filter(user=self.client_a).update(is_active=True)     # contraprueba
        self.assertIsNotNone(client_journey(self.fresh(self.client_a)))

    def test_deactivated_account(self):
        User.objects.filter(pk=self.client_a.pk).update(is_active=False)
        self.assertIsNone(client_journey(self.fresh(self.client_a)))

    def test_minor_by_assessment_age(self):
        InitialAssessment.objects.filter(user=self.client_b).update(age=16)
        self.assertIsNone(self.journey(self.client_b))

    def test_minor_by_profile_birth_date(self):
        from apps.health.models import HealthProfile
        from apps.health.tests.helpers import adult_birth_date
        HealthProfile.objects.filter(user=self.client_a, is_current=True).update(
            birth_date=adult_birth_date(30).replace(year=timezone.localdate().year - 15))
        self.assertIsNone(self.journey())


# ─── No llama a la IA, no escribe, no filtra nada ─────────────────────────────

class NoSideEffectsTests(JourneyTestCase):
    def test_never_calls_the_ai_or_creates_jobs_or_plans(self):
        self.ready(self.client_a)
        before = (PlanGenerationJob.objects.count(), Plan.objects.count())
        with mock.patch('apps.plans.ai.get_ai_client') as get_client, \
                mock.patch('apps.plans.jobs.start_job') as start_job:
            for _ in range(3):
                self.journey()
                self.journey(self.client_b)
        get_client.assert_not_called()
        start_job.assert_not_called()
        self.assertEqual((PlanGenerationJob.objects.count(), Plan.objects.count()), before)

    def test_no_technical_codes_and_no_health_data_in_any_state(self):
        scenarios = []
        self.drop_measures(self.client_b)
        scenarios.append(self.journey(self.client_b))                      # sin nada
        self.grant(self.client_b)
        scenarios.append(self.journey(self.client_b))                      # con consentimiento
        self.ready(self.client_a, conditions=['asthma'], injuries=['knee'], other_condition_text='Dato privado 123')
        scenarios.append(self.journey())                                   # fuera del alcance de alimentación
        self.ready(self.client_a, pregnancy_status='pregnant')
        scenarios.append(self.journey())                                   # bandera roja
        for result in scenarios:
            blob = '\n'.join(strings_of(result))
            self.assertIsNone(TECH_CODES.search(blob), TECH_CODES.search(blob))
            for forbidden in ('consent_health', 'consent_ai', 'missing_steps', 'D0', 'asthma', 'asma', 'knee',
                              'rodilla', 'Dato privado', 'embarazo', self.client_a.username, self.client_a.email):
                self.assertNotIn(forbidden, blob)
            dump = json.dumps(result, ensure_ascii=False)
            for forbidden in ('Dato privado', 'pregnan', 'asthma', self.client_a.email):
                self.assertNotIn(forbidden, dump)

    def test_the_result_is_plain_data(self):
        self.ready(self.client_a)
        json.dumps(self.journey())        # solo textos, números y booleanos

    def test_decided_states_do_not_filter_the_catalog_but_open_ones_do(self):
        """Contraprueba de la eficiencia: con plan aprobado no se filtra; sin plan, sí."""
        self.ready(self.client_a)
        with mock.patch.object(services.rules_catalog, 'filter_exercises',
                               wraps=services.rules_catalog.filter_exercises) as exercises, \
                mock.patch.object(services.rules_foods, 'filter_foods',
                                  wraps=services.rules_foods.filter_foods) as foods:
            self.journey()
            self.assertEqual((exercises.call_count, foods.call_count), (1, 1))
        self.make_exercise(self.client_a, approve=True)
        self.make_nutrition(self.client_a, approve=True)
        with mock.patch.object(services.rules_catalog, 'filter_exercises') as exercises, \
                mock.patch.object(services.rules_foods, 'filter_foods') as foods:
            result = self.journey()
            exercises.assert_not_called()
            foods.assert_not_called()
        self.assertEqual({entry['state'] for entry in result['plans'].values()}, {'approved'})


# ─── `next` seguro ────────────────────────────────────────────────────────────

class SafeNextTests(JourneyTestCase):
    def request_with(self, **data):
        return RequestFactory().get('/x/', data)

    def test_internal_paths_are_accepted(self):
        for target in ('/plans/request/', '/plans/request/?kind=nutrition', '/accounts/dashboard/'):
            with self.subTest(target=target):
                self.assertEqual(safe_next(self.request_with(next=target)), target)

    def test_external_or_malformed_targets_are_ignored(self):
        for target in ('https://evil.example/x', 'http://evil.example', '//evil.example/x', '/\\evil.example',
                       'javascript:alert(1)', 'plans/request/', '', ' /x', '/x\nSet-Cookie: a=b', 'x' * 600,
                       '/' + 'a' * 600):
            with self.subTest(target=target[:40]):
                self.assertIsNone(safe_next(self.request_with(next=target)))

    def test_same_host_absolute_urls_are_not_accepted(self):
        self.assertIsNone(safe_next(self.request_with(next='http://testserver/plans/')))
        self.assertFalse(is_safe_next(self.request_with(), 'http://testserver/plans/'))

    def test_post_wins_over_the_query_string(self):
        request = RequestFactory().post('/x/?next=/query/', {'next': '/body/'})
        self.assertEqual(safe_next(request), '/body/')

    def test_with_next(self):
        self.assertEqual(with_next('/a/', '/b/?k=1'), '/a/?next=/b/%3Fk%3D1')
        self.assertEqual(with_next('/a/?edit=1', '/b/'), '/a/?edit=1&next=/b/')
        self.assertEqual(with_next('/a/', None), '/a/')
        self.assertEqual(with_next('/a/', ''), '/a/')


class ProfileNextTests(JourneyTestCase):
    BACK = '/plans/request/?kind=nutrition'

    def setUp(self):
        super().setUp()
        self.login(self.client_a)
        self.url = reverse('member_health_profile')

    def test_journey_url_for_the_profile_brings_the_next_to_the_form(self):
        self.drop_measures(self.client_a)
        self.grant(self.client_a)
        profile_url = step(client_journey(self.client_a, self.BACK), 'profile')['url']
        response = self.client.get(profile_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['next'], self.BACK)
        self.assertEqual(response.context['mode'], 'member')

    def test_saving_the_profile_goes_back_to_next(self):
        self.grant(self.client_a)
        response = self.client.post(self.url, {**valid_post(), 'next': self.BACK})
        self.assertRedirects(response, self.BACK, fetch_redirect_response=False)
        self.assertTrue(services.health.current_profile(self.client_a))

    def test_next_in_the_query_string_also_works(self):
        self.grant(self.client_a)
        response = self.client.post(with_next(self.url, self.BACK), valid_post())
        self.assertRedirects(response, self.BACK, fetch_redirect_response=False)

    def test_without_next_or_with_an_external_one_the_usual_destination_applies(self):
        self.grant(self.client_a)
        response = self.client.post(self.url, {**valid_post(), 'next': 'https://evil.example/robo'})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        response = self.client.post(self.url, {**valid_post(), 'next': '//evil.example'})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        response = self.client.post(self.url, valid_post())
        self.assertRedirects(response, self.url, fetch_redirect_response=False)

    def test_an_external_next_is_not_offered_to_the_templates(self):
        self.grant(self.client_a)
        response = self.client.get(self.url, {'next': 'https://evil.example/robo'})
        self.assertEqual(response.context['next'], '')

    def test_invalid_post_keeps_the_next_for_the_re_rendered_form(self):
        self.grant(self.client_a)
        response = self.client.post(self.url, {'next': self.BACK})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['errors'])
        self.assertEqual(response.context['next'], self.BACK)
        self.assertFalse(services.health.current_profile(self.client_a))

    def test_without_consent_the_next_survives_the_trip_to_the_consent_page(self):
        for method, kwargs in (('get', {'data': {'next': self.BACK}}), ('post', {'data': {'next': self.BACK}})):
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.url, **kwargs)
                self.assertEqual(response.status_code, 302)
                consent = urlparse(response['Location'])
                self.assertEqual(consent.path, reverse('member_consent', args=[PURPOSE_HEALTH_DATA]))
                back_to_profile = parse_qs(consent.query)['next'][0]
                self.assertEqual(back_to_profile, with_next(self.url, self.BACK))
        # ... y tras aceptar vuelve a la ficha, que todavía recuerda el destino final
        consent_url = reverse('member_consent', args=[PURPOSE_HEALTH_DATA])
        version = services.health.current_text(PURPOSE_HEALTH_DATA).version
        response = self.client.post(consent_url, {'accept': 'on', 'text_version': version,
                                                  'next': with_next(self.url, self.BACK)})
        self.assertRedirects(response, with_next(self.url, self.BACK), fetch_redirect_response=False)
        self.assertEqual(self.client.get(with_next(self.url, self.BACK)).context['next'], self.BACK)

    def test_edit_without_consent_keeps_edit_and_next(self):
        self.ready(self.client_a)
        services.health.revoke_consent(self.client_a, PURPOSE_HEALTH_DATA)
        response = self.client.get(self.url, {'edit': '1', 'next': self.BACK})
        back_to_profile = parse_qs(urlparse(response['Location']).query)['next'][0]
        self.assertEqual(back_to_profile, with_next(self.url + '?edit=1', self.BACK))

    def test_confirming_a_trainer_correction_goes_back_to_next(self):
        self.ready(self.client_a)
        self.make_profile(self.client_a, actor=self.trainer_a)
        detail = self.client.get(self.url, {'next': self.BACK})
        self.assertTrue(detail.context['needs_confirmation'])
        self.assertEqual(detail.context['next'], self.BACK)
        response = self.client.post(reverse('member_health_profile_confirm'), {'next': self.BACK})
        self.assertRedirects(response, self.BACK, fetch_redirect_response=False)
        self.assertFalse(services.health.current_profile(self.client_a).needs_confirmation)

    def test_confirm_without_next_or_with_an_external_one_stays_on_the_profile(self):
        self.ready(self.client_a)
        self.make_profile(self.client_a, actor=self.trainer_a)
        response = self.client.post(reverse('member_health_profile_confirm'), {'next': 'https://evil.example/x'})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        response = self.client.post(reverse('member_health_profile_confirm'))
        self.assertRedirects(response, self.url, fetch_redirect_response=False)

    def test_trainer_edit_form_has_no_next(self):
        self.ready(self.client_a)
        self.login(self.trainer_a)
        response = self.client.get(reverse('trainer_health_profile_edit', args=[self.client_a.pk]),
                                   {'next': self.BACK})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['next'], '')


class MeasurementNextTests(JourneyTestCase):
    BACK = '/plans/request/?kind=exercise'

    def setUp(self):
        super().setUp()
        self.login(self.client_a)
        self.url = reverse('member_measurement_add')

    def test_journey_url_for_the_measures_brings_the_next_to_the_form(self):
        self.ready(self.client_a)
        BodyMeasurement.objects.filter(user=self.client_a).update(date=timezone.localdate() - timedelta(days=45))
        url = step(client_journey(self.client_a, self.BACK), 'measure')['url']
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['next'], self.BACK)

    def test_saving_goes_back_to_next_and_the_measure_counts(self):
        self.ready(self.client_a)
        BodyMeasurement.objects.filter(user=self.client_a).update(date=timezone.localdate() - timedelta(days=45))
        self.assertEqual(step(self.journey(), 'measure')['status'], 'todo')
        response = self.client.post(self.url, {'weight': '61', 'next': self.BACK})
        self.assertRedirects(response, self.BACK, fetch_redirect_response=False)
        self.assertEqual(step(self.journey(), 'measure')['status'], 'done')

    def test_next_in_the_query_string_also_works(self):
        self.grant(self.client_a)
        response = self.client.post(with_next(self.url, self.BACK), {'weight': '61'})
        self.assertRedirects(response, self.BACK, fetch_redirect_response=False)

    def test_without_next_or_with_an_external_one_it_goes_to_the_history(self):
        self.grant(self.client_a)
        history = reverse('member_measurement_list')
        for data in ({'weight': '61'}, {'weight': '61.5', 'next': 'https://evil.example/robo'},
                     {'weight': '62', 'next': '//evil.example'}):
            with self.subTest(data=data):
                self.assertRedirects(self.client.post(self.url, data), history, fetch_redirect_response=False)

    def test_invalid_post_keeps_the_next(self):
        self.grant(self.client_a)
        response = self.client.post(self.url, {'weight': 'abc', 'next': self.BACK})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['next'], self.BACK)

    def test_an_external_next_is_not_offered_to_the_templates(self):
        self.grant(self.client_a)
        self.assertEqual(self.client.get(self.url, {'next': 'https://evil.example/x'}).context['next'], '')

    def test_without_consent_the_consent_page_returns_to_the_measures_and_keeps_the_next(self):
        response = self.client.get(self.url)       # sin next: igual que siempre
        self.assertRedirects(response, f"{reverse('member_consent', args=[PURPOSE_HEALTH_DATA])}?next={self.url}",
                             fetch_redirect_response=False)
        response = self.client.get(self.url, {'next': self.BACK})
        consent = urlparse(response['Location'])
        self.assertEqual(consent.path, reverse('member_consent', args=[PURPOSE_HEALTH_DATA]))
        self.assertEqual(parse_qs(consent.query)['next'][0], with_next(self.url, self.BACK))
        response = self.client.post(self.url, {'weight': '61', 'next': self.BACK})
        self.assertEqual(parse_qs(urlparse(response['Location']).query)['next'][0], with_next(self.url, self.BACK))
        self.assertFalse(BodyMeasurement.objects.filter(user=self.client_a, source='member').exists())


# ─── Panel y «Pedir mi plan» ──────────────────────────────────────────────────

class DashboardTests(JourneyTestCase):
    def test_dashboard_passes_the_journey(self):
        self.ready(self.client_a, basic=True)
        self.login(self.client_a)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        result = response.context['journey']
        self.assertEqual(set(result), TOP_KEYS)
        self.assertEqual(next_of(step(result, 'activity')['url']), DASHBOARD)
        self.assertEqual(result['plans']['exercise']['state'], 'ready')

    def test_dashboard_for_a_brand_new_client(self):
        self.drop_measures(self.client_b)
        self.login(self.client_b)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(statuses(response.context['journey']), ['todo', 'locked', 'locked', 'locked', 'locked'])

    def test_dashboard_renders_without_a_journey(self):
        self.ready(self.client_a)
        self.login(self.client_a)
        for ctx in (override_settings(AI_PLANS_MODE='off'), override_settings(HEALTH_FEATURES_ENABLED=False)):
            with ctx:
                response = self.client.get(reverse('dashboard'))
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.context['journey'])

    def test_trainer_dashboard_is_untouched(self):
        self.login(self.trainer_a)
        self.assertRedirects(self.client.get(reverse('dashboard')), reverse('trainer_dashboard'),
                             fetch_redirect_response=False)

    def test_dashboard_headers_do_not_change(self):
        self.ready(self.client_a)
        self.login(self.client_a)
        with_journey = self.client.get(reverse('dashboard'))
        with mock.patch('apps.plans.journey.client_journey', return_value=None):
            without_journey = self.client.get(reverse('dashboard'))
        self.assertIsNotNone(with_journey.context['journey'])
        for header in ('Cache-Control', 'Vary', 'Expires', 'Pragma', 'Content-Type', 'Content-Disposition'):
            self.assertEqual(with_journey.headers.get(header), without_journey.headers.get(header), header)
        self.assertNotIn('no-store', with_journey.headers.get('Cache-Control', ''))

    def test_dashboard_context_has_no_health_data(self):
        self.ready(self.client_a, pregnancy_status='pregnant', other_condition_text='Dato privado 123')
        self.login(self.client_a)
        response = self.client.get(reverse('dashboard'))
        dump = json.dumps(response.context['journey'], ensure_ascii=False)
        for forbidden in ('Dato privado', 'R02', 'pregnan'):
            self.assertNotIn(forbidden, dump)


class RequestPageTests(JourneyTestCase):
    def test_request_page_adds_the_journey_and_keeps_the_eligibility(self):
        self.drop_measures(self.client_b)
        self.grant(self.client_b)
        self.login(self.client_b)
        response = self.client.get(reverse('member_plan_request'), {'kind': 'nutrition'})
        self.assertEqual(response.status_code, 200)
        result = response.context['journey']
        self.assertEqual(set(result), TOP_KEYS)
        for item in result['steps']:
            if item['status'] == 'todo':
                self.assertEqual(next_of(item['url']), '/plans/request/?kind=nutrition', item['code'])
        eligibility_ctx = response.context['eligibility']
        self.assertEqual(set(eligibility_ctx), {'can_request', 'blocked', 'blocked_message', 'missing'})
        self.assertFalse(eligibility_ctx['can_request'])
        codes = [row['code'] for row in eligibility_ctx['missing']]
        self.assertIn('D02_PROFILE', codes)
        self.assertIn('D03_MEASURE', codes)
        # La lista actual lleva ahora `next` también en la ficha y las medidas
        for row in eligibility_ctx['missing']:
            if row['url']:
                self.assertEqual(next_of(row['url']), '/plans/request/?kind=nutrition', row['code'])

    def test_request_page_for_a_ready_client(self):
        self.ready(self.client_a)
        self.login(self.client_a)
        response = self.client.get(reverse('member_plan_request'))
        self.assertTrue(response.context['eligibility']['can_request'])
        self.assertEqual(statuses(response.context['journey']), ['done'] * 5)

    def test_request_page_without_journey_while_the_legal_texts_are_pending(self):
        self.ready(self.client_a)
        self.publish_new_version(PURPOSE_AI, body='Texto con [RAZÓN SOCIAL] sin completar.')
        self.login(self.client_a)
        response = self.client.get(reverse('member_plan_request'))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['journey'])
        self.assertIn('D00_NOT_AVAILABLE', [row['code'] for row in response.context['eligibility']['missing']])

    def test_post_still_creates_the_job_and_redirects(self):
        self.ready(self.client_a)
        self.login(self.client_a)
        with self.fake_ai(), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('member_plan_request'), {
                'kind': 'exercise', 'request_token': services.new_request_token()})
        job = PlanGenerationJob.objects.get(client=self.client_a)
        self.assertRedirects(response, reverse('member_plan_request_status', args=[job.pk]),
                             fetch_redirect_response=False)


class RequestScreenBlockedTests(JourneyTestCase):
    """«Pedir mi plan» bloqueado muestra el texto JUNTO al botón «Avisar a mi entrenador»: todavía no se
    avisó, así que no puede decir que ya se hizo. Tras pulsar, la pantalla de estado sí conserva el original."""
    GENERIC = ('Por la información de salud que registraste, lo más seguro es que tu médico o nutricionista '
               'te valore antes de armar un plan. Usa el botón para avisar a tu entrenador.')
    FEW_EXERCISES = ('Por ahora no hay suficientes ejercicios compatibles contigo. '
                     'Usa el botón para avisar a tu entrenador.')
    FEW_FOODS = ('Por ahora no hay suficientes alimentos compatibles contigo. '
                 'Usa el botón para avisar a tu entrenador.')
    WELLNESS = 'Los menús de bienestar no cubren esta solicitud. La alimentación clínica está pausada.'

    def blocked_page(self, kind='exercise'):
        self.login(self.client_a)
        response = self.client.get(reverse('member_plan_request'), {'kind': kind})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['eligibility']['blocked'])
        self.assertFalse(response.context['eligibility']['missing'])
        return response

    def assertNoNoticeClaim(self, response):
        for claim in ('ya fue avisado', 'fue avisado', 'te contactará'):
            self.assertNotContains(response, claim)
            self.assertNotIn(claim, response.context['eligibility']['blocked_message'].lower())

    def test_red_flag_text_invites_to_use_the_button(self):
        self.ready(self.client_a, pregnancy_status='pregnant')
        response = self.blocked_page()
        self.assertEqual(response.context['eligibility']['blocked_message'], self.GENERIC)
        self.assertEqual(self.GENERIC, journey_module.MESSAGE_BLOCKED_REQUEST)
        self.assertContains(response, self.GENERIC)
        self.assertContains(response, 'Avisar a mi entrenador')
        self.assertNoNoticeClaim(response)
        self.assertNotContains(response, 'embarazo')

    def test_few_exercises_and_few_foods_texts(self):
        self.ready(self.client_a)
        Exercise.objects.update(is_active=False)
        Food.objects.update(is_active=False)
        exercise = self.blocked_page('exercise')
        self.assertEqual(exercise.context['eligibility']['blocked_message'], self.FEW_EXERCISES)
        self.assertContains(exercise, self.FEW_EXERCISES)
        self.assertNoNoticeClaim(exercise)
        nutrition = self.blocked_page('nutrition')
        self.assertEqual(nutrition.context['eligibility']['blocked_message'], self.FEW_FOODS)
        self.assertContains(nutrition, self.FEW_FOODS)
        self.assertNoNoticeClaim(nutrition)

    def test_out_of_wellness_scope_text_is_unchanged(self):
        self.ready(self.client_a, eating_disorder_history='yes')
        response = self.blocked_page('nutrition')
        self.assertEqual(response.context['eligibility']['blocked_message'], self.WELLNESS)
        self.assertNoNoticeClaim(response)
        self.assertNotContains(response, 'trastorno')

    def test_panel_and_request_screen_use_the_same_causes_with_their_own_closing(self):
        """Sin duplicar: el panel y «Pedir mi plan» salen de `journey.blocked_message`."""
        self.ready(self.client_a, pregnancy_status='pregnant')
        assessment = services.assess(self.client_a, KIND_EXERCISE, self.client_a)
        panel = journey_module.blocked_message(assessment)
        request_screen = journey_module.blocked_message(assessment, on_request_screen=True)
        self.assertTrue(panel.endswith('Habla con tu entrenador.'))
        self.assertTrue(request_screen.endswith('Usa el botón para avisar a tu entrenador.'))
        self.assertEqual(panel.split('plan.')[0], request_screen.split('plan.')[0])

    def test_after_pressing_the_button_the_status_screen_keeps_the_original_text(self):
        self.ready(self.client_a, pregnancy_status='pregnant')
        before = self.blocked_page()
        self.assertNoNoticeClaim(before)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('member_plan_request'), {
                'kind': 'exercise', 'request_token': services.new_request_token()})
        job = PlanGenerationJob.objects.get(client=self.client_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_BLOCKED)
        self.assertRedirects(response, reverse('member_plan_request_status', args=[job.pk]),
                             fetch_redirect_response=False)
        status = self.client.get(response['Location'])
        self.assertEqual(status.context['job_message'], eligibility.BLOCKED_MESSAGE)
        self.assertContains(status, 'Tu entrenador ya fue avisado y te contactará.')
        self.assertIn('ya fue avisado', eligibility.BLOCKED_MESSAGE)     # la constante no cambia


class RequirementLinksTests(JourneyTestCase):
    """Los enlaces «Ir ahora» de la lista de requisitos (la de siempre) ahora vuelven a «Pedir mi plan»."""

    def test_every_requirement_with_a_screen_carries_the_next(self):
        back = '/plans/request/'
        for code in (eligibility.D01_CONSENT_HEALTH, eligibility.D01_CONSENT_AI, eligibility.D02_PROFILE,
                     eligibility.D02_PROFILE_CONFIRM, eligibility.D03_MEASURE, eligibility.D04_INCOHERENT,
                     eligibility.D07_NUTRITION_DETAILS):
            with self.subTest(code=code):
                url = journey_module.requirement_url(code, back)
                self.assertEqual(next_of(url), back)
        for code in (eligibility.D05_QUOTA, eligibility.D06_FEW_EXERCISES, eligibility.D06_FEW_FOODS,
                     eligibility.D00_NOT_AVAILABLE):
            self.assertIsNone(journey_module.requirement_url(code, back))

    def test_edit_links_keep_edit_before_the_next(self):
        url = journey_module.requirement_url(eligibility.D04_INCOHERENT, '/plans/request/')
        self.assertEqual(url, reverse('member_health_profile') + '?edit=1&next=/plans/request/')


# ─── Consultas ────────────────────────────────────────────────────────────────

class QueryCountTests(JourneyTestCase):
    # Tope de consultas que el camino añade al panel. Medido el 2026-11-20 (SQLite de pruebas): 11 (clienta
    # nueva), 14 (lista para pedir: la peor, filtra los dos catálogos), 12 (los dos planes aprobados).
    # El panel solo, sin camino, hace 10. Si sube de aquí, algo repite consultas: revisar antes de subir el tope.
    MAX_JOURNEY_QUERIES = 16

    def panel_queries(self, with_journey=True):
        self.login(self.client_a)
        patcher = None if with_journey else mock.patch('apps.plans.journey.client_journey', return_value=None)
        if patcher:
            patcher.start()
        try:
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(reverse('dashboard'))
        finally:
            if patcher:
                patcher.stop()
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def journey_queries(self, user=None):
        user = user or self.client_a
        user = type(user).objects.get(pk=user.pk)
        user.membership   # el panel ya la tiene cargada
        with CaptureQueriesContext(connection) as queries:
            client_journey(user)
        return len(queries)

    def test_the_journey_adds_a_bounded_number_of_queries_to_the_panel(self):
        self.ready(self.client_a)
        with_journey = self.panel_queries(True)
        without_journey = self.panel_queries(False)
        cost = self.journey_queries()
        self.assertGreater(with_journey, without_journey)
        self.assertLessEqual(cost, self.MAX_JOURNEY_QUERIES, cost)
        # Lo que el camino agrega al panel es, como mucho, su propio costo
        self.assertLessEqual(with_journey - without_journey, self.MAX_JOURNEY_QUERIES)

    def test_cost_by_state(self):
        """Consultas del camino según el estado de la clienta (el panel solo suma esto)."""
        costs = {}
        self.drop_measures(self.client_b)
        costs['nueva, sin nada'] = self.journey_queries(self.client_b)
        self.grant(self.client_b)
        costs['solo con consentimiento de salud'] = self.journey_queries(self.client_b)
        self.ready(self.client_a)
        costs['lista para pedir (los dos planes)'] = self.journey_queries()
        self.make_exercise(self.client_a, approve=True)
        costs['ejercicio aprobado, alimentación lista'] = self.journey_queries()
        self.make_nutrition(self.client_a, approve=True)
        costs['los dos planes aprobados'] = self.journey_queries()
        self.assertLessEqual(max(costs.values()), self.MAX_JOURNEY_QUERIES, costs)
        self.assertLessEqual(costs['los dos planes aprobados'], costs['lista para pedir (los dos planes)'])

    def test_the_cost_does_not_grow_with_more_plans_or_jobs(self):
        self.ready(self.client_a)
        self.request(self.client_a, actor=self.boss)
        one_draft = self.journey_queries()
        for _ in range(4):
            self.request(self.client_a, actor=self.boss)
        self.assertEqual(PlanGenerationJob.objects.filter(client=self.client_a).count(), 5)
        self.assertEqual(self.journey_queries(), one_draft)

    def test_the_cost_with_approved_plans_does_not_grow_with_history_either(self):
        self.ready(self.client_a)
        self.make_exercise(self.client_a, approve=True)
        self.make_nutrition(self.client_a, approve=True)
        base = self.journey_queries()
        self.make_exercise(self.client_a, approve=True)          # versión nueva que reemplaza a la anterior
        self.request_nutrition(self.client_a, actor=self.boss)
        self.assertEqual(self.journey_queries(), base)

    def test_decided_plans_cost_less_than_open_ones(self):
        """Contraprueba: sin plan se filtra el catálogo (más consultas); con planes aprobados no."""
        self.ready(self.client_a)
        open_cost = self.journey_queries()
        self.make_exercise(self.client_a, approve=True)
        self.make_nutrition(self.client_a, approve=True)
        self.assertLess(self.journey_queries(), open_cost)

    def test_each_assessment_runs_once_per_kind(self):
        self.ready(self.client_a)
        with mock.patch.object(services, 'assess', wraps=services.assess) as assess:
            self.journey()
        self.assertEqual(sorted(call.args[1] for call in assess.call_args_list), ['exercise', 'nutrition'])

    def test_the_shared_part_is_computed_once(self):
        self.ready(self.client_a)
        with mock.patch.object(services, 'assess_base', wraps=services.assess_base) as base, \
                mock.patch.object(services, 'feature_available', wraps=services.feature_available) as available:
            self.journey()
        self.assertEqual(base.call_count, 1)
        self.assertEqual(available.call_count, 1)
