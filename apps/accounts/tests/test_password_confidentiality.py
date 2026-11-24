"""Protección de contraseñas con datos ficticios y base exclusiva de pruebas."""
import sys
import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import PBKDF2PasswordHasher, identify_hasher
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.views.debug import ExceptionReporter, SafeExceptionReporterFilter

from apps.accounts import forms, views
from apps.accounts.tests.helpers import ScenarioTestCase


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.PBKDF2PasswordHasher'])
class StoredPasswordTests(TestCase):
    def test_all_roles_store_salted_hashes_and_verify_without_plaintext_storage(self):
        User = get_user_model()
        secret = 'Solo-Pruebas-Hasheadas-7139!'
        encodings = set()
        for name, role, superuser in [('super-test', 'trainer', True),
                                      ('trainer-test', 'trainer', False),
                                      ('client-test', 'member', False)]:
            user = User.objects.create_user(username=name, password=secret, role=role,
                                            is_superuser=superuser, is_staff=role == 'trainer')
            user.refresh_from_db()
            self.assertNotEqual(user.password, secret)
            hasher = identify_hasher(user.password)
            self.assertEqual(hasher.algorithm, 'pbkdf2_sha256')
            self.assertEqual(hasher.decode(user.password)['iterations'], hasher.iterations)
            self.assertTrue(user.check_password(secret))
            encodings.add(user.password)
        self.assertEqual(len(encodings), 3)

    def test_legacy_iterations_upgrade_after_correct_password_verification(self):
        user = get_user_model().objects.create_user(username='legacy-test', password=None)
        hasher = PBKDF2PasswordHasher()
        secret = 'Solo-Pruebas-Legado-7139!'
        user.password = hasher.encode(secret, hasher.salt(), iterations=1000)
        user.save(update_fields=['password'])
        self.assertTrue(user.check_password(secret))
        user.refresh_from_db()
        self.assertEqual(hasher.decode(user.password)['iterations'], hasher.iterations)


class PasswordWidgetTests(SimpleTestCase):
    def test_password_widgets_hide_and_never_repopulate_values(self):
        for form_class in (forms.RegisterForm, forms.TrainerAddClientForm,
                           forms.TrainerClientEditForm, forms.MemberProfileForm,
                           forms.TrainerCreateTrainerForm, forms.TransferAdminForm):
            for name, field in form_class.base_fields.items():
                if name not in views.PASSWORD_FIELDS:
                    continue
                with self.subTest(form=form_class.__name__, field=name):
                    self.assertEqual(field.widget.input_type, 'password')
                    self.assertNotIn('SENTINEL-SECRET', field.widget.render(name, 'SENTINEL-SECRET'))


class CredentialResponseTests(ScenarioTestCase):
    def test_invalid_forms_never_return_credentials_in_html_or_form_context(self):
        secret = 'Privada-Solo-Pruebas-812947!'
        credentials = dict.fromkeys(views.PASSWORD_FIELDS, secret)
        common = {'first_name': '', 'last_name': 'Persona', 'email': 'private-test@example.com',
                  'username': 'private.test', **credentials}
        cases = [
            ('register', (), None),
            ('trainer_add_client', (), self.boss),
            ('trainer_client_edit', (self.client_a.pk,), self.boss),
            ('member_profile_edit', (), self.client_a),
            ('trainer_create_trainer', (), self.boss),
            ('trainer_admin_transfer', (), self.boss),
        ]
        for route, args, actor in cases:
            with self.subTest(route=route):
                self.client.logout()
                if actor is not None:
                    self.client.force_login(actor)
                response = self.client.post(reverse(route, args=args), common)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(secret, response.content.decode())
                self.assertFalse(set(response.context['form']) & set(views.PASSWORD_FIELDS))
                with self.settings(DEBUG=False):
                    filtered = SafeExceptionReporterFilter().get_post_parameters(response.wsgi_request)
                self.assertNotIn(secret, repr(filtered))

    @override_settings(DEBUG=False)
    def test_exception_report_redacts_passwords_and_view_local_variables(self):
        secret = 'private-test-' + uuid.uuid4().hex
        request = RequestFactory().post('/accounts/login/', {'username': 'missing-user',
                                        'password': secret}, HTTP_HOST='localhost')
        request.user = AnonymousUser()
        request.session = {}
        request._messages = FallbackStorage(request)
        with mock.patch.object(views, 'render', side_effect=RuntimeError('test-only failure')):
            try:
                views.login_view(request)
            except RuntimeError:
                kind, error, traceback = sys.exc_info()
                # Excluir el frame del arnés, que deliberadamente conoce el valor ficticio.
                while traceback and traceback.tb_frame.f_code.co_filename == __file__:
                    traceback = traceback.tb_next
                report = ExceptionReporter(request, kind, error, traceback).get_traceback_text()
            else:
                self.fail('No se produjo el fallo de prueba')
        self.assertNotIn(secret, report)
