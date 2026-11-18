"""D-01: planes de una clienta (`trainer_client_plans`; docs/contracts/plan-ia-fase-d.md, sección 5).

El HTML es del frontend: estas pruebas miran el CONTEXTO y los encabezados (mientras la plantilla real
no exista se usa una de reemplazo).
"""
import re
import uuid
from datetime import timedelta
from pathlib import Path

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.permissions import INACTIVE_CLIENT_MESSAGE
from apps.assessments.models import BodyMeasurement
from apps.health import services as health
from apps.health.models import PURPOSE_HEALTH_DATA
from apps.health.tests.helpers import stub_missing_templates
from apps.plans import cloning, overview, texts
from apps.plans.models import KIND_EXERCISE, KIND_NUTRITION, Plan, PlanEvent, PlanGenerationJob

from .helpers import NutritionScenarioTestCase

CONTEXT_KEYS = {'client', 'client_active', 'inactive_message', 'content_visible', 'ai_mode', 'groups', 'generate',
                'jobs', 'jobs_truncated'}
GROUP_KEYS = {'kind', 'label', 'current', 'draft', 'rows', 'rows_truncated', 'total'}
ROW_KEYS = {'plan', 'status_label', 'is_current', 'is_pending', 'origin', 'origin_label', 'parent_version',
            'flags_count', 'stale', 'expired', 'can_clone'}
GENERATE_KEYS = {'label', 'request_token', 'can_request', 'blocked', 'blocked_text', 'blocked_flags', 'missing',
                 'active_job', 'has_pending_draft'}
JOB_KEYS = {'job', 'kind_label', 'status', 'status_label', 'tone', 'summary', 'flag_texts', 'technical_code',
            'requested_by_label', 'plan_id'}
GENERIC = 'No se pudo generar el borrador. Código técnico'
# Consultas de la pantalla (sesión, la clienta, planes, trabajos y las dos evaluaciones de «generar»); fijo
# aunque la clienta tenga muchos planes o trabajos. Si cambia a propósito, se actualiza aquí.
EXPECTED_QUERIES = 35
APPS = Path(__file__).resolve().parents[1]


def make_plan(client, kind, version, status, **extra):
    return Plan.objects.create(client=client, kind=kind, version=version, status=status, **extra)


def make_job(client, kind, status, **extra):
    return PlanGenerationJob.objects.create(
        kind=kind, client=client, requested_by_role=extra.pop('role', 'trainer'), status=status,
        request_token=uuid.uuid4(), **extra)


@stub_missing_templates()
class ClientPlansTestCase(NutritionScenarioTestCase):
    def page(self, client=None, user=None):
        self.login(user or self.trainer_a)
        return self.client.get(reverse('trainer_client_plans', args=[(client or self.client_a).pk]))

    def context(self, client=None, user=None):
        response = self.page(client, user)
        self.assertEqual(response.status_code, 200)
        return response.context

    def group(self, kind, client=None, user=None):
        return next(g for g in self.context(client, user)['groups'] if g['kind'] == kind)


class ClientPlansAuthorizationTests(ClientPlansTestCase):
    def test_matrix(self):
        url = reverse('trainer_client_plans', args=[self.client_a.pk])
        self.client.logout()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])
        self.assertEqual(self.page(user=self.client_a).status_code, 403)
        self.assertEqual(self.page(user=self.trainer_b).status_code, 404)
        for user in (self.trainer_a, self.boss):
            self.assertEqual(self.page(user=user).status_code, 200)
        self.assertEqual(self.page(self.client_free, self.trainer_a).status_code, 404)
        self.assertEqual(self.page(self.client_free, self.boss).status_code, 200)

    def test_post_is_not_allowed_and_responses_are_no_store(self):
        self.login(self.trainer_a)
        url = reverse('trainer_client_plans', args=[self.client_a.pk])
        self.assertEqual(self.client.post(url).status_code, 405)
        self.assertIn('no-store', self.client.get(url)['Cache-Control'])

    def test_feature_off_is_a_404_for_each_switch(self):
        for label, switch in (('salud apagada', {'HEALTH_FEATURES_ENABLED': False}),
                              ('IA apagada', {'AI_PLANS_MODE': 'off'})):
            with self.subTest(label), override_settings(**switch):
                self.assertEqual(self.page().status_code, 404)

    def test_an_erased_client_is_a_404_and_an_inactive_one_is_visible(self):
        User.objects.filter(pk=self.client_a.pk).update(is_active=False)
        context = self.context()
        self.assertFalse(context['client_active'])
        self.assertEqual(context['inactive_message'], INACTIVE_CLIENT_MESSAGE)
        User.objects.filter(pk=self.client_a.pk).update(erased_at=timezone.now())
        self.assertEqual(self.page().status_code, 404)

    def test_the_other_clients_data_never_shows_up(self):
        self.ready(self.client_b)
        make_plan(self.client_b, KIND_EXERCISE, 1, Plan.STATUS_APPROVED)
        make_job(self.client_b, KIND_EXERCISE, PlanGenerationJob.STATUS_FAILED, error_code='timeout')
        context = self.context()
        self.assertEqual([g['total'] for g in context['groups']], [0, 0])
        self.assertEqual(context['jobs'], [])


class ClientPlansContextTests(ClientPlansTestCase):
    def test_exact_shape_of_the_context(self):
        self.ready(self.client_a)
        approved = self.make_exercise(self.client_a, approve=True)
        make_job(self.client_a, KIND_EXERCISE, PlanGenerationJob.STATUS_FAILED, error_code='timeout')
        context = self.context()
        self.assertTrue(CONTEXT_KEYS <= set(context.keys()))  # ContextList: la plantilla real usa extends e include
        built = overview.client_plans_context(self.client_a, self.trainer_a)
        self.assertEqual(set(built), CONTEXT_KEYS - {'client'})
        self.assertEqual(['exercise', 'nutrition'], [g['kind'] for g in built['groups']])
        self.assertEqual(['Plan de ejercicio', 'Plan de alimentación'], [g['label'] for g in built['groups']])
        for group in built['groups']:
            self.assertEqual(set(group), GROUP_KEYS)
        for row in built['groups'][0]['rows']:
            self.assertEqual(set(row), ROW_KEYS)
        self.assertEqual(set(built['generate']), {'exercise', 'nutrition'})
        for info in built['generate'].values():
            self.assertEqual(set(info), GENERATE_KEYS)
        self.assertEqual(set(built['jobs'][0]), JOB_KEYS)
        self.assertEqual(built['groups'][0]['current']['plan'], approved)
        self.assertIn(built['ai_mode'], ('simulated', 'live'))

    def test_empty_client(self):
        context = self.context()
        for group in context['groups']:
            self.assertEqual((group['rows'], group['current'], group['draft'], group['total']), ([], None, None, 0))
            self.assertFalse(group['rows_truncated'])
        self.assertEqual((context['jobs'], context['jobs_truncated']), ([], False))

    def test_versions_statuses_origin_and_flags(self):
        self.ready(self.client_a, injuries=['knee'])
        v1 = make_plan(self.client_a, KIND_EXERCISE, 1, Plan.STATUS_SUPERSEDED)
        v2 = self.make_exercise(self.client_a, approve=True)             # v2 aprobada (la ficha es la vigente)
        Plan.objects.filter(pk=v2.pk).update(parent=v1, valid_until=timezone.localdate() - timedelta(days=1))
        draft = self.make_exercise(self.client_a)                        # v3 borrador de la IA
        group = self.group('exercise')
        self.assertEqual([row['plan'].version for row in group['rows']], [3, 2, 1])
        self.assertEqual((group['current']['plan'], group['draft']['plan'], group['total']), (v2, draft, 3))
        rows = {row['plan'].version: row for row in group['rows']}
        self.assertEqual([rows[v]['status_label'] for v in (1, 2, 3)], ['Reemplazado', 'Aprobado', 'Borrador de la IA'])
        self.assertEqual((rows[2]['is_current'], rows[3]['is_pending'], rows[1]['is_current']), (True, True, False))
        self.assertEqual((rows[2]['parent_version'], rows[2]['origin'], rows[2]['origin_label']),
                         (1, 'ai', 'Regenerado con IA (de la versión 1)'))
        self.assertEqual(rows[3]['origin_label'], 'Generado con IA')
        self.assertEqual(rows[3]['flags_count'], 1)                       # Y04 por la lesión
        self.assertEqual((rows[2]['expired'], rows[3]['expired']), (True, False))
        self.assertEqual((rows[1]['stale'], rows[2]['stale'], rows[3]['stale']), (None, False, False))
        self.assertEqual([rows[v]['can_clone'] for v in (1, 2, 3)], [True, True, False])
        self.assertEqual(self.group('nutrition')['rows'], [])

    def test_stale_when_the_ficha_changed_or_is_gone(self):
        self.ready(self.client_a)
        approved = self.make_exercise(self.client_a, approve=True)
        self.make_profile(self.client_a, injuries=['knee'])
        self.assertTrue(self.group('exercise')['current']['stale'])
        self.assertEqual(Plan.objects.get(pk=approved.pk).status, Plan.STATUS_APPROVED)

    def test_a_copy_is_labelled_with_its_source_version(self):
        self.ready(self.client_a)
        approved = self.make_exercise(self.client_a, approve=True)
        new, _created = cloning.clone_plan(approved, self.trainer_a)
        group = self.group('exercise')
        self.assertEqual(group['draft']['plan'], new)
        self.assertEqual((group['draft']['origin'], group['draft']['origin_label']),
                         ('clone', f'Copia de la versión {approved.version}'))
        self.assertEqual(group['current']['origin'], 'ai')               # el evento está solo en la copia

    def test_only_the_latest_fifteen_versions_are_listed(self):
        for version in range(1, 19):
            make_plan(self.client_a, KIND_NUTRITION, version, Plan.STATUS_ARCHIVED)
        group = self.group('nutrition')
        self.assertEqual((len(group['rows']), group['total'], group['rows_truncated']), (15, 18, True))
        self.assertEqual(group['rows'][0]['plan'].version, 18)

    def test_can_clone_depends_on_the_state_and_the_account(self):
        archived = make_plan(self.client_a, KIND_EXERCISE, 1, Plan.STATUS_ARCHIVED)
        make_plan(self.client_a, KIND_EXERCISE, 2, Plan.STATUS_IN_REVIEW)
        rows = {row['plan'].pk: row for row in self.group('exercise')['rows']}
        self.assertTrue(rows[archived.pk]['can_clone'])
        User.objects.filter(pk=self.client_a.pk).update(is_active=False)
        self.assertFalse(any(row['can_clone'] for row in self.group('exercise')['rows']))


class GenerateInfoTests(ClientPlansTestCase):
    def generate(self, kind=KIND_EXERCISE, client=None, user=None):
        return self.context(client, user)['generate'][kind]

    def test_a_ready_client_can_be_asked_for_both_kinds(self):
        self.ready(self.client_a)
        for kind in (KIND_EXERCISE, KIND_NUTRITION):
            info = self.generate(kind)
            self.assertEqual((info['can_request'], info['blocked'], info['missing']), (True, False, []))
        before = PlanGenerationJob.objects.count()
        self.page()
        self.assertEqual(PlanGenerationJob.objects.count(), before)       # leer NUNCA crea un trabajo

    def test_tokens_are_new_on_each_load_and_different_per_kind(self):
        self.ready(self.client_a)
        first, second = self.context()['generate'], self.context()['generate']
        tokens = {first['exercise']['request_token'], first['nutrition']['request_token'],
                  second['exercise']['request_token'], second['nutrition']['request_token']}
        self.assertEqual(len(tokens), 4)
        for token in tokens:
            uuid.UUID(token)

    def test_missing_requirements_are_in_the_trainers_voice_with_links(self):
        info = self.generate(client=self.client_b, user=self.boss)        # sin consentimiento ni ficha
        self.assertFalse(info['can_request'])
        codes = [row['code'] for row in info['missing']]
        self.assertIn('D01_CONSENT_HEALTH', codes)
        first = info['missing'][0]
        self.assertEqual(first['text'], 'La clienta aún no autorizó el tratamiento de sus datos de salud.')
        self.assertEqual((first['action'], first['url']), ('Pídele que lo autorice desde su cuenta.', None))
        for row in info['missing']:
            self.assertNotIn('tus datos', row['text'])
            self.assertNotIn('Tu ', row['text'] + row['action'])

    def test_old_measurements_link_to_the_trainers_form(self):
        self.ready(self.client_a)
        BodyMeasurement.objects.filter(user=self.client_a).update(date=timezone.localdate() - timedelta(days=45))
        info = self.generate()
        row = next(r for r in info['missing'] if r['code'] == 'D03_MEASURE')
        self.assertEqual(row['url'], reverse('trainer_measurement_add', args=[self.client_a.pk]))
        self.assertEqual(row['text'], texts.TRAINER_REQUIREMENT_TEXTS['D03_MEASURE'][0])
        self.assertFalse(info['can_request'])

    def test_every_requirement_code_has_a_trainer_text(self):
        from apps.plans.rules import eligibility
        codes = {value for name, value in vars(eligibility).items()
                 if name.startswith('D0') and name != 'D06_FEW_EXERCISES' and name != 'D06_FEW_FOODS'}
        self.assertEqual(codes - set(texts.TRAINER_REQUIREMENT_TEXTS), set())

    def test_a_red_flag_blocks_without_creating_anything_and_explains_it_with_consent(self):
        self.ready(self.client_a)
        BodyMeasurement.objects.create(user=self.client_a, trainer=self.trainer_a, weight=40, height='1.65')
        info = self.generate()
        self.assertEqual((info['blocked'], info['can_request']), (True, False))
        self.assertEqual(info['blocked_text'], texts.TRAINER_BLOCKED_GENERIC)
        self.assertTrue(info['blocked_flags'])
        self.assertTrue(all({'code', 'text', 'action'} == set(row) for row in info['blocked_flags']))
        self.assertTrue(all(not row['code'].startswith('D06') for row in info['blocked_flags']))
        self.assertEqual(PlanGenerationJob.objects.count(), 0)

    def test_without_consent_no_flag_text_is_shown(self):
        self.ready(self.client_a)
        BodyMeasurement.objects.create(user=self.client_a, trainer=self.trainer_a, weight=40, height='1.65')
        health.revoke_consent(self.client_a, PURPOSE_HEALTH_DATA)
        context = self.context()
        self.assertFalse(context['content_visible'])
        info = context['generate']['exercise']
        self.assertEqual(info['blocked_flags'], [])
        self.assertFalse(info['can_request'])
        self.assertIn('D01_CONSENT_HEALTH', [row['code'] for row in info['missing']])

    def test_few_foods_and_clinical_scope_have_their_own_texts(self):
        self.assertEqual(overview.blocked_text(('D06_FEW_EXERCISES',)), texts.TRAINER_BLOCKED_TEXTS['D06_FEW_EXERCISES'])
        self.assertEqual(overview.blocked_text(('D06_FEW_FOODS',)), texts.TRAINER_BLOCKED_TEXTS['D06_FEW_FOODS'])
        self.assertEqual(overview.blocked_text(('R05_CARDIAC', 'NUTRITION_CLINICAL_PAUSED')),
                         texts.TRAINER_BLOCKED_TEXTS['NUTRITION_CLINICAL_PAUSED'])
        self.assertEqual(overview.blocked_text(('R05_CARDIAC',)), texts.TRAINER_BLOCKED_GENERIC)

    def test_a_job_in_progress_or_an_inactive_account_disables_the_button(self):
        self.ready(self.client_a)
        job = make_job(self.client_a, KIND_NUTRITION, PlanGenerationJob.STATUS_QUEUED)
        info = self.generate(KIND_NUTRITION)
        self.assertEqual((info['active_job'], info['can_request']), (job, False))
        self.assertTrue(self.generate(KIND_EXERCISE)['can_request'])      # el otro tipo no se afecta
        job.delete()
        User.objects.filter(pk=self.client_a.pk).update(is_active=False)
        self.assertFalse(self.generate()['can_request'])

    def test_pending_draft_is_reported_without_blocking(self):
        self.ready(self.client_a)
        self.make_exercise(self.client_a)
        info = self.generate()
        self.assertEqual((info['has_pending_draft'], info['can_request']), (True, True))
        self.assertFalse(self.generate(KIND_NUTRITION)['has_pending_draft'])


class JobRowsTests(ClientPlansTestCase):
    def test_summaries_tones_and_labels_by_status(self):
        self.ready(self.client_a)
        plan = make_plan(self.client_a, KIND_EXERCISE, 5, Plan.STATUS_DRAFT_AI)
        make_job(self.client_a, KIND_EXERCISE, PlanGenerationJob.STATUS_SUCCEEDED, plan=plan, role='member')
        make_job(self.client_a, KIND_NUTRITION, PlanGenerationJob.STATUS_FAILED, error_code='ai_http_429')
        make_job(self.client_a, KIND_EXERCISE, PlanGenerationJob.STATUS_BLOCKED, flags=['D06_FEW_EXERCISES'])
        make_job(self.client_a, KIND_NUTRITION, PlanGenerationJob.STATUS_RUNNING, progress_done=2, progress_total=4)
        make_job(self.client_a, PlanGenerationJob.KIND_MEAL_ALTERNATIVE, PlanGenerationJob.STATUS_SUCCEEDED)
        rows = {(r['kind_label'], r['status']): r for r in self.context()['jobs']}
        ok = rows[('Plan de ejercicio', 'succeeded')]
        self.assertEqual((ok['summary'], ok['tone'], ok['plan_id'], ok['requested_by_label']),
                         ('Listo: se creó el borrador (versión 5).', 'good', plan.pk, 'Clienta'))
        failed = rows[('Plan de alimentación', 'failed')]
        self.assertEqual((failed['summary'], failed['tone'], failed['technical_code'], failed['status_label']),
                         (texts.JOB_TEXT_BUSY, 'danger', 'ai_http_429', 'Falló'))
        blocked = rows[('Plan de ejercicio', 'blocked')]
        self.assertEqual((blocked['tone'], blocked['summary']), (
            'warning', 'Bloqueado por seguridad: no hay suficientes ejercicios compatibles con la ficha.'))
        self.assertEqual(blocked['flag_texts'], [])                       # D06 ya va en el resumen
        running = rows[('Plan de alimentación', 'running')]
        self.assertEqual((running['tone'], running['summary']), ('info', 'Generando el borrador (paso 2 de 4).'))
        self.assertEqual(rows[('Otra opción de comida', 'succeeded')]['summary'],
                         'Listo: se creó una opción nueva para la clienta.')

    def test_flag_texts_of_a_blocked_job_need_the_consent(self):
        self.ready(self.client_a)
        make_job(self.client_a, KIND_EXERCISE, PlanGenerationJob.STATUS_BLOCKED, flags=['R05_CARDIAC'])
        with_consent = self.context()['jobs'][0]
        self.assertEqual(with_consent['flag_texts'], ['Problema cardiaco declarado (cuestionario o condición).'])
        self.assertEqual(with_consent['summary'], overview.blocked_summary(['R05_CARDIAC']))
        health.revoke_consent(self.client_a, PURPOSE_HEALTH_DATA)
        without = self.context()['jobs'][0]
        self.assertEqual(without['flag_texts'], [])
        self.assertNotIn('cardiaco', without['summary'].lower())

    def test_only_the_latest_twenty_jobs_are_listed_newest_first(self):
        for _ in range(23):
            make_job(self.client_a, KIND_EXERCISE, PlanGenerationJob.STATUS_FAILED, error_code='timeout')
        context = self.context()
        self.assertEqual((len(context['jobs']), context['jobs_truncated']), (20, True))
        stamps = [row['job'].created_at for row in context['jobs']]
        self.assertEqual(stamps, sorted(stamps, reverse=True))

    def test_every_error_code_the_code_can_produce_has_a_friendly_text(self):
        """Si alguien agrega un código de error nuevo, esta prueba obliga a redactar su texto."""
        found = set()
        # Todos los módulos de la IA (también los que se agreguen después) y la cola de trabajos.
        for path in [APPS / 'jobs.py', *sorted((APPS / 'ai').glob('*.py'))]:
            source = path.read_text(encoding='utf-8')
            found |= set(re.findall(r"(?:JobFailed|AI\w*Error|AIInvalidOutput)\(\s*'([a-z_0-9]+)'", source))
        found |= {'ai_http_429', 'ai_http_503', 'ai_http_400', 'ai_http_401', 'ai_http_403', 'ai_http_404',
                  'ai_finish_max_tokens', 'validation_failed:V03_ALLERGEN', 'validation_failed:V04_KCAL',
                  'V01_SCHEMA', 'not_ready:D03_MEASURE', 'not_ready:D07_NUTRITION_DETAILS', 'internal_error',
                  'worker_lost', 'client_inactive', 'timeout'}
        found.discard('blocked')                                          # el estado `blocked` tiene su propio texto
        self.assertGreater(len(found), 15)
        for code in sorted(found):
            with self.subTest(code=code):
                self.assertFalse(overview.failed_text(code).startswith(GENERIC), code)
        self.assertEqual(overview.failed_text('algo_nuevo'), f'{GENERIC}: algo_nuevo.')
        self.assertEqual(overview.failed_text(''), f'{GENERIC}: desconocido.')

    def test_not_ready_names_the_missing_requirement(self):
        self.assertEqual(
            overview.failed_text('not_ready:D03_MEASURE'),
            'Cuando se iba a generar faltaba un requisito: La clienta no tiene medidas (peso y estatura) de los '
            'últimos 30 días.')


class QueryCountTests(ClientPlansTestCase):
    def test_the_number_of_queries_does_not_grow_with_the_data(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.ready(self.client_a)
        self.login(self.trainer_a)
        url = reverse('trainer_client_plans', args=[self.client_a.pk])

        def measure():
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.client.get(url).status_code, 200)
            return len(queries)

        small = measure()
        self.assertEqual(small, EXPECTED_QUERIES)
        for version in range(1, 13):
            kind = KIND_EXERCISE if version % 2 else KIND_NUTRITION
            make_plan(self.client_a, kind, version, Plan.STATUS_ARCHIVED)
            PlanEvent.objects.create(plan=Plan.objects.filter(client=self.client_a).latest('pk'),
                                     action=PlanEvent.ACTION_CLONED)
            make_job(self.client_a, kind, PlanGenerationJob.STATUS_FAILED, error_code='timeout')
        self.assertEqual(measure(), small)
        with self.assertNumQueries(small):
            self.client.get(url)
