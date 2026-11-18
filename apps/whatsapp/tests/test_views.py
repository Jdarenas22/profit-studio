"""Pantallas W-01 a W-03 (las plantillas reales las hace el frontend; aquí se prueba el contexto)."""
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.whatsapp.models import KIND_OPTIN_CONFIRM, WhatsAppContact, WhatsAppMessage

from .helpers import PHONE, WhatsAppTestCase, activate, membership, whatsapp_text

CONTEXT_KEYS = {'status', 'available', 'text_ready', 'phone_display', 'form', 'errors', 'text_version', 'body',
                'can_resend', 'recent', 'privacy_policy_url', 'is_minor', 'is_simulated'}


class SettingsViewTests(WhatsAppTestCase):
    url = reverse('whatsapp_settings')

    def test_access(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)          # anónimo -> login
        self.login(self.client_a)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(CONTEXT_KEYS <= set(response.context.keys()))
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response.context['status'], 'off')
        self.assertTrue(response.context['available'])
        self.assertEqual(response.context['text_version'], whatsapp_text().version)
        with override_settings(WHATSAPP_CLOUD_MODE='off'):
            self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_trainer_uses_panel_template(self):
        self.login(self.trainer_a)
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, 'trainer/whatsapp_settings.html')

    def test_prefills_phone_from_profile(self):
        self.client_a.phone = '300-123-4567'
        self.client_a.save(update_fields=['phone'])
        self.login(self.client_a)
        self.assertEqual(self.client.get(self.url).context['form']['phone'], '+57 300 123 4567')

    def test_enable_errors_rendered_in_context(self):
        self.login(self.client_a)
        response = self.client.post(reverse('whatsapp_enable'), {'phone': '601', 'text_version': whatsapp_text().version})
        self.assertEqual(response.status_code, 200)
        self.assertIn('accept', response.context['errors'])
        response = self.client.post(reverse('whatsapp_enable'),
                                    {'phone': '601', 'accept': 'on', 'text_version': whatsapp_text().version})
        self.assertIn('phone', response.context['errors'])
        self.assertEqual(response.context['form']['phone'], '601')
        response = self.client.post(reverse('whatsapp_enable'), {'phone': PHONE, 'accept': 'on'})
        self.assertIn('general', response.context['errors'])
        self.assertFalse(WhatsAppContact.objects.exists())

    @override_settings(WHATSAPP_CLOUD_SEND_INLINE=True)
    def test_enable_sends_confirmation_inline(self):
        self.login(self.client_a)
        with mock.patch('apps.whatsapp.queue.send_window.in_window', return_value=True), \
                self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('whatsapp_enable'),
                                        {'phone': '300 123 4567', 'accept': 'on', 'text_version': whatsapp_text().version})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        message = WhatsAppMessage.objects.get(kind=KIND_OPTIN_CONFIRM)
        self.assertEqual(message.status, WhatsAppMessage.STATUS_ACCEPTED)
        page = self.client.get(self.url)
        self.assertEqual((page.context['status'], page.context['phone_display']), ('pending', '+57 300 123 4567'))
        self.assertFalse(page.context['can_resend'])

    def test_resend_and_disable(self):
        self.login(self.client_a)
        self.client.post(reverse('whatsapp_enable'), {'phone': PHONE, 'accept': 'on', 'text_version': whatsapp_text().version})
        response = self.client.post(reverse('whatsapp_resend_confirmation'), follow=True)
        self.assertContains(response, 'Espera unos minutos')
        self.client.post(reverse('whatsapp_disable'))
        self.assertEqual(WhatsAppContact.objects.get().status, WhatsAppContact.STATUS_OPTED_OUT)

    def test_disable_allowed_when_off(self):
        activate(self.client_a)
        self.login(self.client_a)
        with override_settings(WHATSAPP_CLOUD_MODE='off'):
            response = self.client.post(reverse('whatsapp_disable'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(WhatsAppContact.objects.get().status, WhatsAppContact.STATUS_OPTED_OUT)

    def test_get_not_allowed_on_actions(self):
        self.login(self.client_a)
        for name in ('whatsapp_enable', 'whatsapp_disable', 'whatsapp_resend_confirmation'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 405)


class DashboardTests(WhatsAppTestCase):
    def test_dashboard_status(self):
        membership(self.client_a, timezone.localdate() + timedelta(days=20))
        self.login(self.client_a)
        self.assertEqual(self.client.get(reverse('dashboard')).context['whatsapp_status'], 'off')
        activate(self.client_a)
        self.assertEqual(self.client.get(reverse('dashboard')).context['whatsapp_status'], 'active')
        with override_settings(WHATSAPP_CLOUD_MODE='off'):
            self.assertIsNone(self.client.get(reverse('dashboard')).context['whatsapp_status'])

    def test_context_processor_flag(self):
        self.login(self.client_a)
        self.assertTrue(self.client.get(reverse('whatsapp_settings')).context['WHATSAPP_NOTICES_ENABLED'])
