"""Mensajes flash vs. errores pintados junto al campo (vistas de accounts).

Regla: un error que la plantilla ya pinta junto al input (`errors.<campo>`) NO se repite
como mensaje flash; uno que la plantilla no pinta SÍ se avisa arriba con `messages`.
Las tuplas *_RENDERED de apps/accounts/views.py declaran qué campos pinta cada plantilla.
"""
import shutil
import tempfile
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils.html import escape

from apps.accounts import views

from .helpers import ScenarioTestCase
from .test_input_validation import STRONG, messages_of


class InlineErrorAssertions:
    def assert_inline_not_flashed(self, response, field, label):
        """El error está en el contexto, se ve en el HTML (pintado) y NO hay flash con su etiqueta."""
        self.assertEqual(response.status_code, 200)
        self.assertIn(field, response.context['errors'])
        self.assertContains(response, escape(response.context['errors'][field]))
        self.assertFalse([m for m in messages_of(response) if label in m], messages_of(response))

    def assert_flashed(self, response, field, label):
        self.assertEqual(response.status_code, 200)
        self.assertIn(field, response.context['errors'])
        self.assertTrue(any(label in m for m in messages_of(response)), messages_of(response))


class RegisterFlashTests(InlineErrorAssertions, ScenarioTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def post(self, **over):
        data = {
            'first_name': 'Ana', 'last_name': 'Pérez', 'email': 'ana.flash@example.com',
            'username': 'ana.flash', 'phone': '+57 300 123 4567', 'gender': 'F',
            'password1': STRONG, 'password2': STRONG, 'training_goal': 'cardio',
        }
        data.update(over)
        return self.client.post(reverse('register'), data)

    def test_inline_fields_are_not_duplicated(self):
        self.assert_inline_not_flashed(self.post(phone='abc'), 'phone', 'Teléfono')
        self.assert_inline_not_flashed(self.post(gender='X'), 'gender', 'Género')
        self.assert_inline_not_flashed(self.post(training_goal='hackear'), 'training_goal', 'Meta')

    def test_field_not_painted_by_the_template_is_flashed(self):
        self.assert_flashed(self.post(interested_plan='1' * 30), 'interested_plan', 'Plan de interés')


class TrainerAddClientFlashTests(InlineErrorAssertions, ScenarioTestCase):
    def setUp(self):
        self.login(self.trainer_a)

    def post(self, **over):
        data = {'first_name': 'Nueva', 'last_name': 'Persona', 'email': 'nueva.flash@example.com',
                'username': 'nueva.flash', 'password': STRONG}
        data.update(over)
        return self.client.post(reverse('trainer_add_client'), data)

    def test_inline_fields_are_not_duplicated(self):
        self.assert_inline_not_flashed(self.post(phone='letras'), 'phone', 'Teléfono')
        self.assert_inline_not_flashed(self.post(gender='Z'), 'gender', 'Género')

    def test_field_not_painted_by_the_template_is_flashed(self):
        self.assert_flashed(self.post(activate_plan='99999'), 'activate_plan', 'Plan')


class TrainerClientEditFlashTests(InlineErrorAssertions, ScenarioTestCase):
    def setUp(self):
        self.login(self.trainer_a)
        self.url = reverse('trainer_client_edit', args=[self.client_a.pk])

    def post(self, **over):
        data = {'first_name': 'Cliente', 'last_name': 'Editado', 'email': 'client_a@example.com',
                'username': 'client_a'}
        data.update(over)
        return self.client.post(self.url, data)

    def test_inline_fields_are_not_duplicated(self):
        self.assert_inline_not_flashed(self.post(phone='xx'), 'phone', 'Teléfono')
        self.assert_inline_not_flashed(self.post(gender='Z'), 'gender', 'Género')

    def test_field_not_painted_by_the_template_is_flashed(self):
        self.assert_flashed(self.post(interested_plan='abc'), 'interested_plan', 'Plan de interés')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='profit-tests-flash-'))
class ProfileFlashTests(InlineErrorAssertions, ScenarioTestCase):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.login(self.trainer_a)    # el campo "bio" solo existe para entrenadores
        self.url = reverse('member_profile_edit')

    def post(self, **over):
        data = {'first_name': 'Cliente', 'last_name': 'Perfil', 'email': 'trainer_a@example.com'}
        data.update(over)
        return self.client.post(self.url, data)

    def test_inline_fields_are_not_duplicated(self):
        self.assert_inline_not_flashed(self.post(phone='letras'), 'phone', 'Teléfono')
        self.assert_inline_not_flashed(self.post(gender='Z'), 'gender', 'Género')
        self.assert_inline_not_flashed(self.post(bio='x' * 1501), 'bio', 'Descripción')
        fake = SimpleUploadedFile('foto.jpg', b'no es imagen', content_type='image/jpeg')
        self.assert_inline_not_flashed(self.post(profile_photo=fake), 'profile_photo', 'Foto')

    def test_field_not_in_the_rendered_list_is_flashed(self):
        # Todos los campos del formulario se pintan; se comprueba el mecanismo quitando uno de la lista
        rendered = tuple(f for f in views.PROFILE_RENDERED if f != 'phone')
        with mock.patch.object(views, 'PROFILE_RENDERED', rendered):
            self.assert_flashed(self.post(phone='letras'), 'phone', 'Teléfono')


class TrainerStaffFlashTests(InlineErrorAssertions, ScenarioTestCase):
    def setUp(self):
        self.login(self.boss)

    def create(self, **over):
        data = {'first_name': 'Nuevo', 'last_name': 'Entrenador', 'email': 'nuevo.flash@example.com',
                'username': 'nuevo.flash', 'password': STRONG, 'password2': STRONG}
        data.update(over)
        return self.client.post(reverse('trainer_create_trainer'), data)

    def edit(self, **over):
        data = {'first_name': 'Ok', 'last_name': 'X', 'email': 'trainer_a@example.com'}
        data.update(over)
        return self.client.post(reverse('trainer_edit_trainer', args=[self.trainer_a.pk]), data)

    def test_create_inline_fields_are_not_duplicated(self):
        self.assert_inline_not_flashed(self.create(phone='xyz'), 'phone', 'Teléfono')
        self.assert_inline_not_flashed(self.create(bio='x' * 1501), 'bio', 'Descripción')

    def test_create_field_not_in_the_rendered_list_is_flashed(self):
        rendered = tuple(f for f in views.TRAINER_FORM_RENDERED if f != 'bio')
        with mock.patch.object(views, 'TRAINER_FORM_RENDERED', rendered):
            self.assert_flashed(self.create(bio='x' * 1501), 'bio', 'Descripción')

    def test_edit_inline_fields_are_not_duplicated(self):
        self.assert_inline_not_flashed(self.edit(phone='xyz'), 'phone', 'Teléfono')
        self.assert_inline_not_flashed(self.edit(bio='x' * 1501), 'bio', 'Descripción')
        fake = SimpleUploadedFile('foto.jpg', b'no es imagen', content_type='image/jpeg')
        self.assert_inline_not_flashed(self.edit(profile_photo=fake), 'profile_photo', 'Foto')

    def test_edit_field_not_in_the_rendered_list_is_flashed(self):
        rendered = tuple(f for f in views.TRAINER_EDIT_RENDERED if f != 'bio')
        with mock.patch.object(views, 'TRAINER_EDIT_RENDERED', rendered):
            self.assert_flashed(self.edit(bio='x' * 1501), 'bio', 'Descripción')
