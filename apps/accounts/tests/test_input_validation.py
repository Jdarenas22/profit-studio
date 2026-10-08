"""Validación de entradas de accounts: registro, clientes, perfil y entrenadores.

Ninguna entrada inválida debe producir un 500: la vista responde 200 con `errors`
(y un aviso en `messages`) y no crea ni modifica nada.
"""
import io
import shutil
import tempfile
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from PIL import Image

from apps.accounts.models import User

from .helpers import PASSWORD, ScenarioTestCase

STRONG = 'Clave-Nueva-5566!'


def png_bytes(size=(8, 8)):
    buf = io.BytesIO()
    Image.new('RGB', size, 'red').save(buf, 'PNG')
    return buf.getvalue()


def messages_of(response):
    return [str(m) for m in response.context['messages']]


class RegisterBase(ScenarioTestCase):
    def setUp(self):
        cache.clear()          # el límite de frecuencia vive en la caché
        self.addCleanup(cache.clear)

    def payload(self, **over):
        data = {
            'first_name': 'Ana', 'last_name': 'Pérez', 'email': 'Ana.Perez@Example.com',
            'username': 'Ana.Perez', 'phone': '+57 300 123 4567', 'gender': 'F',
            'password1': STRONG, 'password2': STRONG, 'training_goal': 'cardio',
        }
        data.update(over)
        return data

    def register(self, **over):
        return self.client.post(reverse('register'), self.payload(**over))


class RegisterValidationTests(RegisterBase):
    def test_valid_registration_normalizes_email_and_username(self):
        response = self.register()
        self.assertRedirects(response, reverse('register_success'), fetch_redirect_response=False)
        user = User.objects.get(username='ana.perez')
        self.assertEqual(user.email, 'ana.perez@example.com')
        self.assertEqual(user.role, User.ROLE_MEMBER)
        self.assertEqual(user.gender, 'F')
        self.assertEqual(user.training_goal, 'cardio')
        self.assertTrue(user.check_password(STRONG))

    def assert_rejected(self, field, **over):
        before = User.objects.count()
        response = self.register(**over)
        self.assertEqual(response.status_code, 200, over)
        self.assertIn(field, response.context['errors'], over)
        self.assertEqual(User.objects.count(), before, over)
        return response

    def test_blank_fields_are_reported_per_field(self):
        response = self.client.post(reverse('register'), {})
        self.assertEqual(response.status_code, 200)
        for field in ('first_name', 'last_name', 'email', 'username', 'password1'):
            self.assertIn(field, response.context['errors'])

    def test_weak_passwords_are_rejected_by_django_validators(self):
        for weak in ('password', '12345678', '58203917', 'abc', 'ana.perez1'):
            with self.subTest(password=weak):
                self.assert_rejected('password1', password1=weak, password2=weak)

    def test_password_similar_to_username_is_rejected(self):
        self.assert_rejected('password1', username='adrigarcia', first_name='Adri',
                             password1='adrigarcia1', password2='adrigarcia1')

    def test_password_mismatch_and_too_long(self):
        self.assert_rejected('password2', password2=STRONG + 'x')
        long_pw = 'Aa1!' * 100
        self.assert_rejected('password1', password1=long_pw, password2=long_pw)

    def test_username_rules(self):
        for bad in ('abc', 'con espacio', 'mal<script>', 'ñandú.01', 'x' * 31, "o'brien", 'a@b.com'):
            with self.subTest(username=bad):
                self.assert_rejected('username', username=bad)

    def test_name_email_phone_rules(self):
        self.assert_rejected('first_name', first_name='<script>alert(1)</script>')
        self.assert_rejected('first_name', first_name='A' * 10000)
        self.assert_rejected('last_name', last_name='12345')
        for bad in ('no-es-correo', 'a@', 'a' * 300 + '@example.com'):
            with self.subTest(email=bad):
                self.assert_rejected('email', email=bad)
        for bad in ('abc', '123', '+' * 10, '1' * 40):
            with self.subTest(phone=bad):
                self.assert_rejected('phone', phone=bad)

    def test_gender_and_goal_must_be_valid_choices(self):
        self.assert_rejected('gender', gender='X')
        self.assert_rejected('training_goal', training_goal='hackear')

    def test_errors_not_shown_next_to_fields_are_flashed(self):
        response = self.register(phone='abc')
        self.assertTrue(any('Teléfono' in m for m in messages_of(response)))

    def test_stale_or_garbage_plan_is_ignored_not_fatal(self):
        for i, plan in enumerate(('abc', '99999', '-1', ' ')):
            with self.subTest(plan=plan):
                cache.clear()
                self.client.logout()   # el registro anterior dejó la sesión iniciada
                response = self.register(username=f'plan_{i}_user', email=f'plan{i}@example.com',
                                         interested_plan=plan)
                self.assertEqual(response.status_code, 302)
                self.assertIsNone(User.objects.get(username=f'plan_{i}_user').interested_plan)

    def test_valid_plan_is_saved(self):
        self.register(interested_plan=str(self.plan.pk))
        self.assertEqual(User.objects.get(username='ana.perez').interested_plan, self.plan)

    def test_existing_email_gets_generic_message_without_confirming_the_account(self):
        response = self.register(email='CLIENT_A@example.com', username='otro.usuario')
        self.assertEqual(response.status_code, 200)
        message = response.context['errors']['email']
        self.assertNotIn('Ya existe', message)
        self.assertNotIn('ya está registrado', message.lower())
        self.assertFalse(User.objects.filter(username='otro.usuario').exists())

    def test_existing_username_is_reported_so_the_person_can_pick_another(self):
        response = self.register(username='CLIENT_A')
        self.assertIn('ya está en uso', response.context['errors']['username'])


class RegisterRateLimitTests(RegisterBase):
    def test_successful_registrations_are_limited_per_ip(self):
        with mock.patch('apps.accounts.views.REGISTER_SUCCESS_LIMIT', 2):
            for i in range(2):
                self.client.logout()
                response = self.register(username=f'persona{i}x', email=f'persona{i}@example.com')
                self.assertEqual(response.status_code, 302, i)
            self.client.logout()
            before = User.objects.count()
            response = self.register(username='persona3x', email='persona3@example.com')
        self.assertEqual(response.status_code, 429)
        self.assertIn('Retry-After', response)
        self.assertEqual(User.objects.count(), before)
        self.assertTrue(any('demasiados intentos' in m.lower() for m in messages_of(response)))

    def test_without_the_limit_the_same_requests_succeed(self):
        """Contraprueba: con un límite alto, el tercer registro NO se bloquea."""
        with mock.patch('apps.accounts.views.REGISTER_SUCCESS_LIMIT', 50):
            for i in range(3):
                self.client.logout()
                response = self.register(username=f'persona{i}y', email=f'personay{i}@example.com')
                self.assertEqual(response.status_code, 302, i)

    def test_failed_attempts_are_limited_too(self):
        with mock.patch('apps.accounts.views.REGISTER_ATTEMPTS_LIMIT', 3):
            codes = [self.register(email='no-es-correo').status_code for _ in range(5)]
        self.assertEqual(codes, [200, 200, 200, 429, 429])

    def test_limit_is_per_ip(self):
        with mock.patch('apps.accounts.views.REGISTER_ATTEMPTS_LIMIT', 1):
            self.assertEqual(self.register(email='malo').status_code, 200)
            self.assertEqual(self.register(email='malo').status_code, 429)
            other = self.client.post(reverse('register'), self.payload(email='malo'),
                                     REMOTE_ADDR='203.0.113.9')
            self.assertEqual(other.status_code, 200)

    def test_cache_failure_does_not_block_registration(self):
        with mock.patch('apps.accounts.ratelimit.cache.add', side_effect=RuntimeError('caché caída')),                 self.assertLogs('apps.accounts.ratelimit', 'ERROR'):
            self.assertEqual(self.register().status_code, 302)


class TrainerAddClientValidationTests(ScenarioTestCase):
    def setUp(self):
        self.login(self.trainer_a)

    def payload(self, **over):
        data = {'first_name': 'Nueva', 'last_name': 'Persona', 'email': 'nueva@example.com',
                'username': 'nueva.persona', 'password': STRONG}
        data.update(over)
        return data

    def post(self, **over):
        return self.client.post(reverse('trainer_add_client'), self.payload(**over))

    def test_valid_client_with_plan_gets_membership(self):
        response = self.post(activate_plan=str(self.plan.pk), phone='3001234567', gender='M')
        created = User.objects.get(username='nueva.persona')
        self.assertRedirects(response, reverse('trainer_client_detail', args=[created.pk]))
        self.assertTrue(created.membership.is_valid)
        self.assertEqual(created.assigned_trainer, self.trainer_a)

    def test_invalid_inputs_never_500_and_create_nothing(self):
        cases = {
            'password': ['', 'corta', 'password', '12345678', 'nueva.persona1', 'x' * 200],
            'username': ['', 'abc', 'con espacio', '<b>x</b>', 'x' * 40],
            'email': ['', 'sin-arroba', 'a' * 300 + '@e.com'],
            'first_name': ['', '<script>', 'A' * 5000],
            'gender': ['Z'],
            'activate_plan': ['abc', '99999', '-1'],
            'phone': ['letras'],
        }
        before = User.objects.count()
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value=value[:20]):
                    response = self.post(**{field: value})
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(field, response.context['errors'])
        self.assertEqual(User.objects.count(), before)

    def test_inactive_plan_cannot_be_activated(self):
        self.plan.is_active = False
        self.plan.save()
        response = self.post(activate_plan=str(self.plan.pk))
        self.assertIn('activate_plan', response.context['errors'])
        self.assertFalse(User.objects.filter(username='nueva.persona').exists())

    def test_duplicates_keep_specific_messages_for_trainers(self):
        response = self.post(email='CLIENT_A@example.com', username='client_a')
        self.assertEqual(response.context['errors']['email'], 'Ya existe una cuenta con ese correo.')
        self.assertEqual(response.context['errors']['username'], 'Nombre de usuario ya en uso.')

    def test_error_keys_match_what_the_template_reads(self):
        response = self.post(first_name='', password='')
        self.assertEqual(response.context['errors']['first_name'], 'Requerido.')
        self.assertEqual(response.context['errors']['password'], 'Mínimo 8 caracteres.')
        self.assertEqual(response.context['form']['username'], 'nueva.persona')   # se conserva lo escrito


class TrainerClientEditValidationTests(ScenarioTestCase):
    def setUp(self):
        self.login(self.trainer_a)
        self.url = reverse('trainer_client_edit', args=[self.client_a.pk])

    def payload(self, **over):
        data = {'first_name': 'Cliente', 'last_name': 'Editado', 'email': 'client_a@example.com',
                'username': 'client_a'}
        data.update(over)
        return data

    def test_weak_new_password_is_rejected_and_old_one_kept(self):
        for weak in ('password', '12345678', 'corta', 'client_a99'):
            with self.subTest(new_password=weak):
                response = self.client.post(self.url, self.payload(new_password=weak))
                self.assertEqual(response.status_code, 200)
                self.assertIn('new_password', response.context['errors'])
                self.assertTrue(User.objects.get(pk=self.client_a.pk).check_password(PASSWORD))

    def test_strong_new_password_is_applied(self):
        response = self.client.post(self.url, self.payload(new_password=STRONG))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.get(pk=self.client_a.pk).check_password(STRONG))

    def test_legacy_username_that_would_fail_today_rules_can_stay_unchanged(self):
        User.objects.filter(pk=self.client_a.pk).update(username='ab')
        response = self.client.post(self.url, self.payload(username='ab', first_name='Otro'))
        self.assertEqual(response.status_code, 302)
        # ...pero un cambio de usuario sí debe cumplir las reglas actuales
        response = self.client.post(self.url, self.payload(username='xy'))
        self.assertIn('username', response.context['errors'])

    def test_invalid_inputs_do_not_modify_the_client(self):
        for field, value in (('email', 'nope'), ('username', 'client_b'), ('gender', 'Z'),
                             ('interested_plan', 'abc'), ('phone', 'xx'), ('first_name', '')):
            with self.subTest(field=field):
                response = self.client.post(self.url, self.payload(**{field: value}))
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context['errors'])
        fresh = User.objects.get(pk=self.client_a.pk)
        self.assertEqual((fresh.first_name, fresh.username, fresh.gender), ('Client_a', 'client_a', ''))

    def test_email_belonging_to_another_user_is_rejected(self):
        response = self.client.post(self.url, self.payload(email='CLIENT_B@example.com'))
        self.assertEqual(response.context['errors']['email'], 'Ya existe una cuenta con ese correo.')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='profit-tests-'))
class MemberProfileValidationTests(ScenarioTestCase):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.login(self.client_a)
        self.url = reverse('member_profile_edit')

    def payload(self, **over):
        data = {'first_name': 'Cliente', 'last_name': 'Perfil', 'email': 'client_a@example.com'}
        data.update(over)
        return data

    def test_wrong_current_password_and_weak_new_password(self):
        response = self.client.post(self.url, self.payload(
            current_password='incorrecta', new_password=STRONG, confirm_password=STRONG))
        self.assertIn('current_password', response.context['errors'])
        for weak in ('password', '12345678', 'corta'):
            with self.subTest(new_password=weak):
                response = self.client.post(self.url, self.payload(
                    current_password=PASSWORD, new_password=weak, confirm_password=weak))
                self.assertIn('new_password', response.context['errors'])
        response = self.client.post(self.url, self.payload(
            current_password=PASSWORD, new_password=STRONG, confirm_password='otra'))
        self.assertIn('confirm_password', response.context['errors'])
        self.assertTrue(User.objects.get(pk=self.client_a.pk).check_password(PASSWORD))

    def test_password_change_keeps_the_session(self):
        response = self.client.post(self.url, self.payload(
            current_password=PASSWORD, new_password=STRONG, confirm_password=STRONG))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.get(pk=self.client_a.pk).check_password(STRONG))
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_fake_image_with_jpg_extension_is_rejected(self):
        fake = SimpleUploadedFile('foto.jpg', b'esto no es una imagen', content_type='image/jpeg')
        response = self.client.post(self.url, self.payload(profile_photo=fake))
        self.assertEqual(response.status_code, 200)
        self.assertIn('profile_photo', response.context['errors'])
        self.assertFalse(User.objects.get(pk=self.client_a.pk).profile_photo)

    def test_valid_image_is_accepted_and_script_extension_is_not(self):
        ok = SimpleUploadedFile('foto.png', png_bytes(), content_type='image/png')
        self.assertEqual(self.client.post(self.url, self.payload(profile_photo=ok)).status_code, 302)
        self.assertTrue(User.objects.get(pk=self.client_a.pk).profile_photo)
        sneaky = SimpleUploadedFile('foto.php', png_bytes(), content_type='image/png')
        response = self.client.post(self.url, self.payload(profile_photo=sneaky))
        self.assertIn('profile_photo', response.context['errors'])

    def test_oversized_image_is_rejected_before_parsing(self):
        big = SimpleUploadedFile('grande.png', b'0' * (5 * 1024 * 1024 + 1), content_type='image/png')
        response = self.client.post(self.url, self.payload(profile_photo=big))
        self.assertIn('pesa demasiado', response.context['errors']['profile_photo'])

    def test_bio_is_only_saved_for_trainers_and_is_limited(self):
        self.client.post(self.url, self.payload(bio='intento'))
        self.assertEqual(User.objects.get(pk=self.client_a.pk).bio, '')
        self.login(self.trainer_a)
        response = self.client.post(self.url, self.payload(email='trainer_a@example.com', bio='x' * 1501))
        self.assertIn('bio', response.context['errors'])
        response = self.client.post(self.url, self.payload(email='trainer_a@example.com', bio='Hola'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.get(pk=self.trainer_a.pk).bio, 'Hola')

    def test_cannot_take_another_users_email(self):
        response = self.client.post(self.url, self.payload(email='CLIENT_B@example.com'))
        self.assertIn('email', response.context['errors'])


class TrainerStaffValidationTests(ScenarioTestCase):
    def setUp(self):
        self.login(self.boss)

    def test_create_trainer_validates_password_and_fields(self):
        url = reverse('trainer_create_trainer')
        base = {'first_name': 'Nuevo', 'last_name': 'Entrenador', 'email': 'nuevo.ent@example.com',
                'username': 'nuevo.ent', 'password': STRONG, 'password2': STRONG}
        before = User.objects.count()
        for field, value in (('password', 'password'), ('password', '12345678'), ('password2', 'distinta'),
                             ('username', 'a b'), ('email', 'x'), ('bio', 'x' * 1501), ('phone', 'xyz')):
            with self.subTest(field=field):
                data = dict(base, **{field: value})
                if field == 'password':
                    data['password2'] = value
                response = self.client.post(url, data)
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context['errors'])
        self.assertEqual(User.objects.count(), before)
        response = self.client.post(url, base)
        self.assertRedirects(response, reverse('trainer_staff_list'))
        created = User.objects.get(username='nuevo.ent')
        self.assertEqual((created.role, created.is_staff, created.is_superuser),
                         (User.ROLE_TRAINER, True, False))

    def test_edit_trainer_validation(self):
        url = reverse('trainer_edit_trainer', args=[self.trainer_a.pk])
        response = self.client.post(url, {'first_name': '', 'last_name': 'X', 'email': 'bad'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.context['errors']), {'first_name', 'email'})
        response = self.client.post(url, {'first_name': 'Ok', 'last_name': 'X', 'email': 'CLIENT_A@example.com'})
        self.assertIn('email', response.context['errors'])
        self.assertNotEqual(User.objects.get(pk=self.trainer_a.pk).first_name, 'Ok')


class SpecificExceptionTests(ScenarioTestCase):
    def test_client_without_membership_still_renders_without_catching_everything(self):
        self.login(self.trainer_a)
        response = self.client.get(reverse('trainer_client_detail', args=[self.client_a.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['membership'])
        self.login(self.client_a)
        response = self.client.get(reverse('dashboard'))
        self.assertTemplateUsed(response, 'accounts/no_membership.html')

    def test_unexpected_errors_are_not_swallowed_as_missing_membership(self):
        """Antes `except Exception` convertía cualquier bug en "sin membresía"."""
        from datetime import date, timedelta
        from apps.memberships.models import Membership
        Membership.objects.create(user=self.client_a, plan=self.plan, start_date=date.today(),
                                  end_date=date.today() + timedelta(days=5))
        self.login(self.client_a)
        with mock.patch('apps.memberships.models.Membership.is_valid',
                        new_callable=mock.PropertyMock, side_effect=RuntimeError('bug real')):
            with self.assertRaises(RuntimeError):
                self.client.get(reverse('dashboard'))
