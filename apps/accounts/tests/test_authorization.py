"""Autorización por cliente: un entrenador solo ve a SUS clientes; la superusuaria ve todo.

Cubre todas las vistas que reciben el pk de un cliente, o de algo que pertenece a
un cliente (valoración, rutina, día, ejercicio de rutina, membresía, medición, pago manual).
"""
from unittest import mock

from django.contrib.auth.hashers import check_password
from django.urls import reverse

from apps.accounts.models import User
from apps.assessments.models import InitialAssessment, BodyMeasurement
from apps.memberships.models import Membership
from apps.payments.models import ManualPayment
from apps.routines.models import Routine, RoutineDay, RoutineExercise

from .helpers import ScenarioTestCase, PASSWORD


class ClientScopedViewsTests(ScenarioTestCase):
    """GET de cada vista de cliente: propio=200, ajeno o sin asignar=404, superusuaria=200."""

    def urls_for(self, client, assessment, routine):
        return {
            'client_detail': reverse('trainer_client_detail', args=[client.pk]),
            'client_edit': reverse('trainer_client_edit', args=[client.pk]),
            'client_delete': reverse('trainer_client_delete', args=[client.pk]),
            'assessment_create': reverse('trainer_assessment_create', args=[client.pk]),
            'assessment_detail': reverse('trainer_assessment_detail', args=[assessment.pk]),
            'measurement_add': reverse('trainer_measurement_add', args=[client.pk]),
            'membership_manage': reverse('trainer_membership_manage', args=[client.pk]),
            'manual_payment_add': reverse('trainer_manual_payment_add', args=[client.pk]),
            'manual_payment_list_client': reverse('trainer_manual_payment_list') + f'?client={client.pk}',
            'routine_builder': reverse('trainer_routine_builder', args=[routine.pk]),
        }

    def own(self):
        return self.urls_for(self.client_a, self.assessment_a, self.routine_a)

    def foreign(self):
        return self.urls_for(self.client_b, self.assessment_b, self.routine_b)

    def unassigned(self):
        return self.urls_for(self.client_free, self.assessment_free, self.routine_free)

    def test_trainer_opens_own_client_pages(self):
        self.login(self.trainer_a)
        for name, url in self.own().items():
            with self.subTest(view=name):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_trainer_gets_404_on_other_trainers_client(self):
        self.login(self.trainer_a)
        for name, url in self.foreign().items():
            with self.subTest(view=name):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_trainer_gets_404_on_unassigned_client(self):
        """Decisión de negocio: los sin asignar solo los ve la superusuaria."""
        self.login(self.trainer_a)
        for name, url in self.unassigned().items():
            with self.subTest(view=name):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_superuser_opens_every_client(self):
        self.login(self.boss)
        for label, urls in (('own', self.own()), ('foreign', self.foreign()), ('free', self.unassigned())):
            for name, url in urls.items():
                with self.subTest(owner=label, view=name):
                    self.assertEqual(self.client.get(url).status_code, 200)

    def test_member_cannot_use_trainer_views(self):
        self.login(self.client_a)
        for name, url in self.own().items():
            with self.subTest(view=name):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_anonymous_is_sent_to_login(self):
        for name, url in self.own().items():
            with self.subTest(view=name):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response.url)


class ForeignClientWritesAreBlockedTests(ScenarioTestCase):
    """Los POST sobre clientes ajenos o sin asignar no cambian nada."""

    def setUp(self):
        self.login(self.trainer_a)

    def test_cannot_delete_foreign_client(self):
        for victim in (self.client_b, self.client_free):
            response = self.client.post(reverse('trainer_client_delete', args=[victim.pk]))
            self.assertEqual(response.status_code, 404)
            self.assertTrue(User.objects.filter(pk=victim.pk).exists())

    def test_cannot_change_foreign_client_password(self):
        before = User.objects.get(pk=self.client_b.pk).password
        response = self.client.post(reverse('trainer_client_edit', args=[self.client_b.pk]), {
            'first_name': 'X', 'last_name': 'Y', 'email': 'x@example.com',
            'username': 'client_b', 'new_password': 'Nueva-Clave-7788!',
        })
        self.assertEqual(response.status_code, 404)
        after = User.objects.get(pk=self.client_b.pk).password
        self.assertEqual(before, after)
        self.assertTrue(check_password(PASSWORD, after))

    def test_can_edit_own_client(self):
        response = self.client.post(reverse('trainer_client_edit', args=[self.client_a.pk]), {
            'first_name': 'Nuevo', 'last_name': 'Nombre', 'email': 'client_a@example.com',
            'username': 'client_a',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.get(pk=self.client_a.pk).first_name, 'Nuevo')

    def test_cannot_create_assessment_for_foreign_client(self):
        count = InitialAssessment.objects.count()
        response = self.client.post(reverse('trainer_assessment_create', args=[self.client_b.pk]), {
            'age': 30, 'sex': 'F', 'weight': 60, 'height': '1.65', 'goal': 'x',
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(InitialAssessment.objects.count(), count)

    def test_cannot_add_dixon_to_foreign_assessment(self):
        response = self.client.post(
            reverse('trainer_assessment_detail', args=[self.assessment_b.pk]),
            {'p0': 70, 'p1': 100, 'p2': 80},
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(hasattr(InitialAssessment.objects.get(pk=self.assessment_b.pk), 'dixon_test'))

    def test_cannot_add_measurement_for_foreign_client(self):
        count = BodyMeasurement.objects.count()
        response = self.client.post(reverse('trainer_measurement_add', args=[self.client_b.pk]), {'weight': '70'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(BodyMeasurement.objects.count(), count)

    def test_can_add_measurement_for_own_client(self):
        count = BodyMeasurement.objects.count()
        response = self.client.post(reverse('trainer_measurement_add', args=[self.client_a.pk]), {'weight': '70'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(BodyMeasurement.objects.count(), count + 1)

    def test_cannot_manage_foreign_membership(self):
        response = self.client.post(reverse('trainer_membership_manage', args=[self.client_b.pk]), {
            'action': 'activate', 'plan_id': self.plan.pk,
        })
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Membership.objects.filter(user=self.client_b).exists())

    def test_can_manage_own_membership(self):
        response = self.client.post(reverse('trainer_membership_manage', args=[self.client_a.pk]), {
            'action': 'activate', 'plan_id': self.plan.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Membership.objects.filter(user=self.client_a).exists())

    def test_cannot_register_manual_payment_for_foreign_client(self):
        count = ManualPayment.objects.count()
        response = self.client.post(reverse('trainer_manual_payment_add', args=[self.client_b.pk]), {
            'amount': '10000', 'method': 'cash', 'payment_date': '2026-10-01',
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(ManualPayment.objects.count(), count)

    def test_can_register_manual_payment_for_own_client(self):
        count = ManualPayment.objects.count()
        response = self.client.post(reverse('trainer_manual_payment_add', args=[self.client_a.pk]), {
            'amount': '10000', 'method': 'cash', 'payment_date': '2026-10-01',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ManualPayment.objects.count(), count + 1)


class RoutineAuthorizationTests(ScenarioTestCase):
    def setUp(self):
        self.login(self.trainer_a)

    def test_routine_endpoints_on_foreign_routine_return_404(self):
        r, d, i = self.routine_b, self.day_b, self.item_b
        checks = [
            ('get', reverse('trainer_routine_builder', args=[r.pk]), {}),
            ('post', reverse('trainer_routine_delete', args=[r.pk]), {}),
            ('post', reverse('trainer_add_day', args=[r.pk]), {'day_name': 'Nuevo'}),
            ('post', reverse('trainer_delete_day', args=[r.pk, d.pk]), {}),
            ('post', reverse('trainer_add_exercise_to_day', args=[r.pk, d.pk]),
             {'exercise': self.exercise.pk}),
            ('post', reverse('trainer_remove_exercise', args=[r.pk, d.pk, i.pk]), {}),
        ]
        for method, url, data in checks:
            with self.subTest(url=url):
                response = getattr(self.client, method)(url, data)
                self.assertEqual(response.status_code, 404)
        self.assertTrue(Routine.objects.get(pk=r.pk).is_active)
        self.assertTrue(RoutineDay.objects.filter(pk=d.pk).exists())
        self.assertTrue(RoutineExercise.objects.filter(pk=i.pk).exists())
        self.assertEqual(r.days.count(), 1)
        self.assertEqual(d.exercises.count(), 1)

    def test_routine_of_unassigned_client_is_404(self):
        response = self.client.get(reverse('trainer_routine_builder', args=[self.routine_free.pk]))
        self.assertEqual(response.status_code, 404)

    def test_own_routine_flow_works(self):
        r, d, i = self.routine_a, self.day_a, self.item_a
        self.assertEqual(self.client.get(reverse('trainer_routine_builder', args=[r.pk])).status_code, 200)
        self.assertEqual(
            self.client.post(reverse('trainer_add_day', args=[r.pk]), {'day_name': 'Dia 2'}).status_code, 200)
        self.assertEqual(r.days.count(), 2)
        self.assertEqual(self.client.post(
            reverse('trainer_add_exercise_to_day', args=[r.pk, d.pk]),
            {'exercise': self.exercise.pk, 'sets': 3, 'reps': '10', 'rest_seconds': 60}).status_code, 200)
        self.assertEqual(d.exercises.count(), 2)
        self.assertEqual(
            self.client.post(reverse('trainer_remove_exercise', args=[r.pk, d.pk, i.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse('trainer_delete_day', args=[r.pk, d.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse('trainer_routine_delete', args=[r.pk])).status_code, 302)
        self.assertFalse(Routine.objects.get(pk=r.pk).is_active)

    def test_routine_create_only_for_accessible_member(self):
        url = reverse('trainer_routine_create')
        before = Routine.objects.count()
        # Cliente ajeno, sin asignar, otro entrenador, la superusuaria, uno mismo, vacío y basura
        for bad in (self.client_b.pk, self.client_free.pk, self.trainer_b.pk, self.boss.pk,
                    self.trainer_a.pk, '', 'abc', '99999'):
            with self.subTest(client=bad):
                response = self.client.post(url, {'name': 'Mala', 'client': bad})
                self.assertEqual(response.status_code, 404)
        self.assertEqual(Routine.objects.count(), before)

        response = self.client.post(url, {'name': 'Buena', 'client': self.client_a.pk})
        self.assertEqual(response.status_code, 302)
        routine = Routine.objects.get(name='Buena')
        self.assertEqual(routine.user, self.client_a)
        self.assertEqual(routine.trainer, self.trainer_a)

    def test_routine_create_form_lists_only_own_clients(self):
        response = self.client.get(reverse('trainer_routine_create'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['clients']), [self.client_a])

    def test_superuser_can_create_routine_for_any_member_but_not_for_trainers(self):
        self.login(self.boss)
        url = reverse('trainer_routine_create')
        for member in (self.client_a, self.client_b, self.client_free):
            response = self.client.post(url, {'name': f'R-{member.pk}', 'client': member.pk})
            self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.post(url, {'name': 'T', 'client': self.trainer_a.pk}).status_code, 404)


class ListsAndSelectorsTests(ScenarioTestCase):
    def test_client_list_per_role(self):
        url = reverse('trainer_clients')
        self.login(self.trainer_a)
        self.assertEqual(set(self.client.get(url).context['clients']), {self.client_a})
        self.login(self.trainer_b)
        self.assertEqual(set(self.client.get(url).context['clients']), {self.client_b})
        self.login(self.boss)
        response = self.client.get(url)
        self.assertEqual(set(response.context['clients']), {self.client_a, self.client_b, self.client_free})
        self.assertEqual(response.context['unassigned_count'], 1)

    def test_dashboard_numbers_per_role(self):
        url = reverse('trainer_dashboard')
        self.login(self.trainer_a)
        ctx = self.client.get(url).context
        self.assertEqual(ctx['total_clients'], 1)
        self.assertEqual(ctx['total_routines'], 1)
        self.assertEqual(set(ctx['recent_clients']), {self.client_a})
        self.assertEqual([a.pk for a in ctx['recent_assessments']], [self.assessment_a.pk])
        self.assertEqual(ctx['unassigned_clients'], 0)
        self.login(self.boss)
        ctx = self.client.get(url).context
        self.assertEqual(ctx['total_clients'], 3)
        self.assertEqual(ctx['total_routines'], 3)
        self.assertEqual(ctx['unassigned_clients'], 1)

    def test_routine_list_per_role(self):
        url = reverse('trainer_routine_list')
        self.login(self.trainer_a)
        self.assertEqual({r.pk for r in self.client.get(url).context['routines']}, {self.routine_a.pk})
        self.login(self.boss)
        self.assertEqual(len(self.client.get(url).context['routines']), 3)

    def test_manual_payment_list_is_scoped(self):
        url = reverse('trainer_manual_payment_list')
        self.login(self.trainer_a)
        self.assertEqual({p.user for p in self.client.get(url).context['payments']}, {self.client_a})
        self.assertEqual(self.client.get(url + f'?client={self.client_b.pk}').status_code, 404)
        self.assertEqual(self.client.get(url + '?client=abc').status_code, 404)
        self.login(self.boss)
        self.assertEqual(len(self.client.get(url).context['payments']), 3)

    def test_assessment_list_only_accessible_clients(self):
        """assessment_list no muestra valoraciones de clientes que ya no son accesibles."""
        # Una valoración que A hizo a un cliente que luego pasó a B
        InitialAssessment.objects.create(
            user=self.client_b, trainer=self.trainer_a, age=30, sex='F', weight=60,
            height='1.65', goal='x',
        )
        with mock.patch('apps.assessments.views.render') as fake_render:
            fake_render.return_value = mock.sentinel.response
            from apps.assessments.views import assessment_list
            assessment_list(mock.Mock(user=self.trainer_a))
        shown = list(fake_render.call_args.args[2]['assessments'])
        self.assertEqual([a.user for a in shown], [self.client_a])


class ClientCreationTests(ScenarioTestCase):
    def post_new_client(self, username):
        return self.client.post(reverse('trainer_add_client'), {
            'first_name': 'Nueva', 'last_name': 'Persona', 'email': f'{username}@example.com',
            'username': username, 'password': 'Clave-Nueva-5566!',
        })

    def test_client_created_by_trainer_is_assigned_to_that_trainer(self):
        self.login(self.trainer_a)
        response = self.post_new_client('creada_por_a')
        created = User.objects.get(username='creada_por_a')
        self.assertEqual(created.assigned_trainer, self.trainer_a)
        self.assertEqual(created.role, User.ROLE_MEMBER)
        self.assertRedirects(response, reverse('trainer_client_detail', args=[created.pk]))
        # ...y lo ve A, pero no B
        self.assertEqual(self.client.get(reverse('trainer_client_detail', args=[created.pk])).status_code, 200)
        self.login(self.trainer_b)
        self.assertEqual(self.client.get(reverse('trainer_client_detail', args=[created.pk])).status_code, 404)

    def test_self_registered_client_is_unassigned_and_only_boss_sees_it(self):
        response = self.client.post(reverse('register'), {
            'first_name': 'Auto', 'last_name': 'Registro', 'email': 'auto@example.com',
            'username': 'autoregistro', 'password1': 'Clave-Nueva-5566!', 'password2': 'Clave-Nueva-5566!',
        })
        self.assertEqual(response.status_code, 302)
        created = User.objects.get(username='autoregistro')
        self.assertIsNone(created.assigned_trainer)
        self.client.logout()
        url = reverse('trainer_client_detail', args=[created.pk])
        self.login(self.trainer_a)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.login(self.boss)
        self.assertEqual(self.client.get(url).status_code, 200)


class SuperuserOnlyViewsTests(ScenarioTestCase):
    def staff_urls(self):
        return [
            reverse('trainer_staff_list'),
            reverse('trainer_create_trainer'),
            reverse('trainer_edit_trainer', args=[self.trainer_b.pk]),
            reverse('trainer_assign_client', args=[self.client_a.pk]),
        ]

    def test_regular_trainer_is_redirected_away(self):
        self.login(self.trainer_a)
        for url in self.staff_urls():
            for method in ('get', 'post'):
                with self.subTest(url=url, method=method):
                    response = getattr(self.client, method)(url)
                    self.assertRedirects(response, reverse('trainer_dashboard'),
                                         fetch_redirect_response=False)

    def test_trainer_cannot_create_or_edit_or_assign(self):
        self.login(self.trainer_a)
        users_before = User.objects.count()
        self.client.post(reverse('trainer_create_trainer'), {
            'first_name': 'X', 'last_name': 'Y', 'email': 'x@example.com', 'username': 'nuevo_entrenador',
            'password': 'Clave-Nueva-5566!', 'password2': 'Clave-Nueva-5566!',
        })
        self.assertEqual(User.objects.count(), users_before)
        self.client.post(reverse('trainer_edit_trainer', args=[self.trainer_b.pk]), {
            'first_name': 'Hackeado', 'last_name': 'Y', 'email': 'h@example.com',
        })
        self.assertNotEqual(User.objects.get(pk=self.trainer_b.pk).first_name, 'Hackeado')
        # Un entrenador no puede quedarse con clientes sin asignar ni reasignar los suyos
        self.client.post(reverse('trainer_assign_client', args=[self.client_free.pk]),
                         {'trainer_id': self.trainer_a.pk})
        self.assertIsNone(User.objects.get(pk=self.client_free.pk).assigned_trainer)
        self.client.post(reverse('trainer_assign_client', args=[self.client_a.pk]), {'trainer_id': ''})
        self.assertEqual(User.objects.get(pk=self.client_a.pk).assigned_trainer, self.trainer_a)

    def test_member_gets_403(self):
        self.login(self.client_a)
        for url in self.staff_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_anonymous_goes_to_login(self):
        for url in self.staff_urls():
            with self.subTest(url=url):
                self.assertRedirects(self.client.get(url), reverse('login'),
                                     fetch_redirect_response=False)

    def test_superuser_has_access_and_is_the_only_one_who_assigns(self):
        self.login(self.boss)
        for url in self.staff_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(reverse('trainer_assign_client', args=[self.client_free.pk]),
                                    {'trainer_id': self.trainer_a.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.get(pk=self.client_free.pk).assigned_trainer, self.trainer_a)
        # a partir de ahí A lo ve
        self.login(self.trainer_a)
        self.assertEqual(
            self.client.get(reverse('trainer_client_detail', args=[self.client_free.pk])).status_code, 200)

    def test_assign_rejects_garbage_and_non_trainers(self):
        self.login(self.boss)
        url = reverse('trainer_assign_client', args=[self.client_free.pk])
        for bad in ('abc', str(self.client_a.pk), '99999'):
            with self.subTest(trainer_id=bad):
                self.assertEqual(self.client.post(url, {'trainer_id': bad}).status_code, 404)
        self.assertIsNone(User.objects.get(pk=self.client_free.pk).assigned_trainer)


class PaymentReturnIsOwnedTests(ScenarioTestCase):
    def test_payment_return_does_not_leak_other_users_payment(self):
        from apps.payments.models import Payment
        Payment.objects.create(user=self.client_b, plan=self.plan, amount_cents=10000000,
                               wompi_transaction_id='tx-b', status=Payment.STATUS_APPROVED)
        self.login(self.client_a)
        response = self.client.get(reverse('payment_return') + '?id=tx-b')
        self.assertIsNone(response.context['payment'])
