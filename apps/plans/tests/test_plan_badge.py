"""Contador «Planes por revisar» del menú del entrenador (fase D, tramo D1 de docs/contracts/plan-ia.md).

La vista `trainer_plan_badge` devuelve un fragmento con `count`; el HTML es del frontend, así que
estas pruebas miran el CONTEXTO y los encabezados, no el HTML (mientras la plantilla no exista se usa
la de reemplazo de `stub_missing_templates`). Lo esencial: el contador y la bandeja cuentan con la
MISMA función (`services.pending_review_count`) y el entrenador solo cuenta lo de SUS clientas.
"""
from django.db.models import Max
from django.urls import reverse

from apps.health.tests.helpers import stub_missing_templates
from apps.plans import services
from apps.plans.models import KIND_EXERCISE, MealOption, Plan

from .helpers import NutritionScenarioTestCase

BADGE_TEMPLATE = 'trainer/partials/plan_badge.html'
INBOX_KEYS = ('rows', 'status', 'status_choices', 'pending_count', 'active_jobs', 'attention_jobs', 'ai_mode')


@stub_missing_templates()
class BadgeScenarioTestCase(NutritionScenarioTestCase):
    """client_a es de trainer_a, client_b de trainer_b y client_free solo la ve la superusuaria."""

    def make_plan(self, client, status, kind=KIND_EXERCISE):
        last = Plan.objects.filter(client=client, kind=kind).aggregate(last=Max('version'))['last'] or 0
        return Plan.objects.create(client=client, kind=kind, version=last + 1, status=status)

    def regenerated_approved(self, client, options=1):
        """Plan de alimentación aprobado con `options` opciones activas por revisar (lo que deja
        «otra opción» de la clienta)."""
        plan = self.approved_nutrition(client)
        ids = list(MealOption.objects.filter(slot__nutrition_plan__plan=plan, is_active=True)
                   .order_by('pk').values_list('pk', flat=True)[:options])
        self.assertEqual(len(ids), options)
        MealOption.objects.filter(pk__in=ids).update(needs_review=True)
        return plan

    def badge(self, user):
        self.login(user)
        return self.client.get(reverse('trainer_plan_badge'))

    def count(self, user):
        response = self.badge(user)
        self.assertEqual(response.status_code, 200)
        return response.context['count']


class BadgeAuthorizationTests(BadgeScenarioTestCase):
    def test_anonymous_goes_to_login(self):
        response = self.client.get(reverse('trainer_plan_badge'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_clients_are_forbidden(self):
        for client in (self.client_a, self.client_free):
            with self.subTest(user=client.username):
                self.assertEqual(self.badge(client).status_code, 403)

    def test_trainers_get_the_fragment_with_an_integer_count_and_no_store(self):
        for user in (self.trainer_a, self.trainer_b, self.boss):
            with self.subTest(user=user.username):
                response = self.badge(user)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, BADGE_TEMPLATE)
                self.assertIn('no-store', response['Cache-Control'])
                self.assertIsInstance(response.context['count'], int)
                self.assertEqual(response.context['count'], 0)

    def test_only_get_is_accepted(self):
        self.login(self.trainer_a)
        for method in ('post', 'put', 'patch', 'delete'):
            with self.subTest(method=method):
                response = getattr(self.client, method)(reverse('trainer_plan_badge'))
                self.assertEqual(response.status_code, 405)
                self.assertIn('no-store', response['Cache-Control'])

    def test_it_is_a_404_when_the_feature_is_off(self):
        for overrides in ({'AI_PLANS_MODE': 'off'}, {'HEALTH_FEATURES_ENABLED': False}):
            with self.settings(**overrides):
                self.login(self.trainer_a)
                with self.subTest(settings=overrides):
                    self.assertEqual(self.client.get(reverse('trainer_plan_badge')).status_code, 404)
                self.client.logout()
                with self.subTest(settings=overrides, user='anónimo'):
                    self.assertEqual(self.client.get(reverse('trainer_plan_badge')).status_code, 404)

    def test_the_route_is_the_one_in_the_contract(self):
        self.assertEqual(reverse('trainer_plan_badge'), '/plans/trainer/inbox/badge/')


class BadgeCountTests(BadgeScenarioTestCase):
    def test_zero_when_nothing_is_pending(self):
        self.assertEqual(self.count(self.trainer_a), 0)
        self.assertEqual(self.count(self.boss), 0)

    def test_drafts_and_plans_in_review_count(self):
        self.make_plan(self.client_a, Plan.STATUS_DRAFT_AI)
        self.assertEqual(self.count(self.trainer_a), 1)
        self.make_plan(self.client_a, Plan.STATUS_IN_REVIEW)
        self.assertEqual(self.count(self.trainer_a), 2)

    def test_a_real_nutrition_draft_counts(self):
        self.nutrition_draft(self.client_a)
        self.assertEqual(self.count(self.trainer_a), 1)

    def test_approved_plans_without_options_to_review_do_not_count(self):
        self.approved_nutrition(self.client_a)
        self.make_plan(self.client_a, Plan.STATUS_APPROVED)
        self.assertEqual(self.count(self.trainer_a), 0)

    def test_an_approved_plan_with_an_option_to_review_counts(self):
        self.regenerated_approved(self.client_a)
        self.assertEqual(self.count(self.trainer_a), 1)

    def test_an_approved_plan_counts_once_however_many_options_wait(self):
        self.regenerated_approved(self.client_a, options=3)
        self.assertEqual(MealOption.objects.filter(needs_review=True).count(), 3)
        self.assertEqual(self.count(self.trainer_a), 1)

    def test_an_option_that_is_no_longer_active_does_not_make_the_plan_count(self):
        self.regenerated_approved(self.client_a)
        MealOption.objects.filter(needs_review=True).update(is_active=False)
        self.assertEqual(self.count(self.trainer_a), 0)

    def test_archived_and_superseded_plans_do_not_count(self):
        self.make_plan(self.client_a, Plan.STATUS_ARCHIVED)
        self.make_plan(self.client_a, Plan.STATUS_SUPERSEDED)
        self.assertEqual(self.count(self.trainer_a), 0)
        plan = self.regenerated_approved(self.client_a)
        self.assertEqual(self.count(self.trainer_a), 1)
        for status in (Plan.STATUS_ARCHIVED, Plan.STATUS_SUPERSEDED):
            with self.subTest(status=status):
                Plan.objects.filter(pk=plan.pk).update(status=status)   # con opciones por revisar, pero ya no vigente
                self.assertEqual(self.count(self.trainer_a), 0)

    def test_each_trainer_counts_only_the_plans_of_their_clients(self):
        self.make_plan(self.client_a, Plan.STATUS_DRAFT_AI)
        self.make_plan(self.client_b, Plan.STATUS_IN_REVIEW)
        self.make_plan(self.client_b, Plan.STATUS_DRAFT_AI)
        self.make_plan(self.client_free, Plan.STATUS_DRAFT_AI)
        self.assertEqual(self.count(self.trainer_a), 1)
        self.assertEqual(self.count(self.trainer_b), 2)
        self.assertEqual(self.count(self.boss), 4)          # la superusuaria ve todo, también a la clienta sin asignar

    def test_a_regenerated_plan_of_another_trainers_client_does_not_count(self):
        self.regenerated_approved(self.client_b)
        self.assertEqual(self.count(self.trainer_a), 0)
        self.assertEqual(self.count(self.trainer_b), 1)
        self.assertEqual(self.count(self.boss), 1)

    def test_the_count_is_not_capped_at_99(self):
        # El tope visual («99+») lo pone la plantilla; el contador entrega el número real
        Plan.objects.bulk_create([Plan(client=self.client_a, kind=KIND_EXERCISE, version=n,
                                       status=Plan.STATUS_DRAFT_AI) for n in range(1, 101)])
        self.assertEqual(self.count(self.trainer_a), 100)

    def test_shared_functions_and_their_cost(self):
        plan = self.regenerated_approved(self.client_a, options=2)
        self.make_plan(self.client_a, Plan.STATUS_DRAFT_AI)
        self.assertEqual(services.regenerated_plan_ids(self.trainer_a), {plan.pk})
        self.assertEqual(services.regenerated_plan_ids(self.trainer_b), set())
        self.assertEqual(services.regenerated_plan_ids(self.client_a), set())      # una clienta no cuenta nada
        self.assertEqual(services.pending_review_count(self.client_a), 0)
        with self.assertNumQueries(2):                       # ids por revisar + la cuenta; sin consultas por plan
            self.assertEqual(services.pending_review_count(self.trainer_a), 2)
        ids = services.regenerated_plan_ids(self.trainer_a)
        with self.assertNumQueries(1):                       # la bandeja ya tiene los ids: no los pide otra vez
            self.assertEqual(services.pending_review_count(self.trainer_a, ids), 2)


class BadgeMatchesInboxTests(BadgeScenarioTestCase):
    def mixed_scenario(self):
        """Mezcla los tres casos que cuentan y los que no.
        client_a: aprobado con opción por revisar (1), borrador de ejercicio (1), archivado (0).
        client_b: aprobado sin nada por revisar (0), en revisión (1).
        client_free: en revisión (1), reemplazado (0)."""
        self.regenerated_approved(self.client_a)
        self.make_plan(self.client_a, Plan.STATUS_DRAFT_AI)
        self.make_plan(self.client_a, Plan.STATUS_ARCHIVED)
        self.approved_nutrition(self.client_b)
        self.make_plan(self.client_b, Plan.STATUS_IN_REVIEW)
        self.make_plan(self.client_free, Plan.STATUS_IN_REVIEW)
        self.make_plan(self.client_free, Plan.STATUS_SUPERSEDED)

    def test_the_badge_and_the_inbox_give_the_same_number(self):
        self.mixed_scenario()
        for user, expected in ((self.trainer_a, 2), (self.trainer_b, 1), (self.boss, 4)):
            with self.subTest(user=user.username):
                badge = self.count(user)
                inbox = self.client.get(reverse('trainer_plan_inbox'))
                self.assertEqual(inbox.status_code, 200)
                self.assertEqual(badge, expected)
                self.assertEqual(inbox.context['pending_count'], badge)
                self.assertEqual(len(inbox.context['rows']), badge)    # la lista y el número salen de lo mismo

    def test_the_inbox_number_does_not_depend_on_the_filter(self):
        self.mixed_scenario()
        badge = self.count(self.boss)
        for status in ('', 'draft_ai', 'in_review', 'approved', 'otra-cosa'):
            with self.subTest(status=status):
                inbox = self.client.get(reverse('trainer_plan_inbox'), {'status': status})
                self.assertEqual(inbox.context['pending_count'], badge)

    def test_the_inbox_keeps_its_context(self):
        self.mixed_scenario()
        self.login(self.trainer_a)
        response = self.client.get(reverse('trainer_plan_inbox'))
        for key in INBOX_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, response.context)
        rows = response.context['rows']
        self.assertEqual(sorted(row['plan'].status for row in rows), ['approved', 'draft_ai'])
        self.assertEqual({row['plan'].status: row['client_regenerated'] for row in rows},
                         {'approved': True, 'draft_ai': False})
        self.assertEqual(response.context['status'], '')
        filtered = self.client.get(reverse('trainer_plan_inbox'), {'status': 'approved'})
        self.assertEqual(filtered.context['status'], 'approved')
        self.assertEqual([row['plan'].status for row in filtered.context['rows']], ['approved'])
