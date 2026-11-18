import hashlib
import hmac
import json

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.whatsapp.webhook import valid_challenge, valid_signature


class WebhookAuthenticationTests(TestCase):
    secret = 'test-app-secret'
    verify_token = 'test-verify-token-with-more-than-32-characters'

    def signature(self, body):
        digest = hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
        return f'sha256={digest}'

    def test_signature_requires_exact_raw_body_and_constant_format(self):
        body = b'{"object":"whatsapp_business_account"}'
        self.assertTrue(valid_signature(body, self.signature(body), self.secret))
        self.assertFalse(valid_signature(body + b' ', self.signature(body), self.secret))
        self.assertFalse(valid_signature(body, self.signature(body).removeprefix('sha256='), self.secret))
        self.assertFalse(valid_signature(body, self.signature(body), ''))

    def test_challenge_rejects_unexpected_characters_and_long_values(self):
        self.assertTrue(valid_challenge('challenge_123-abc'))
        self.assertFalse(valid_challenge('bad/challenge'))
        self.assertFalse(valid_challenge('x' * 129))

    @override_settings(
        WHATSAPP_CLOUD_MODE='live',
        WHATSAPP_CLOUD_APP_SECRET=secret,
        WHATSAPP_CLOUD_VERIFY_TOKEN=verify_token,
    )
    def test_post_rejects_missing_or_invalid_signature(self):
        body = json.dumps({'object': 'whatsapp_business_account'}).encode()
        url = reverse('whatsapp_webhook')
        self.assertEqual(self.client.post(url, body, content_type='application/json').status_code, 401)
        response = self.client.post(
            url, body, content_type='application/json', HTTP_X_HUB_SIGNATURE_256=self.signature(body)[:-1] + '0'
        )
        self.assertEqual(response.status_code, 401)

    @override_settings(
        WHATSAPP_CLOUD_MODE='live',
        WHATSAPP_CLOUD_APP_SECRET=secret,
        WHATSAPP_CLOUD_VERIFY_TOKEN=verify_token,
    )
    def test_post_accepts_signed_payload(self):
        body = json.dumps({'object': 'whatsapp_business_account', 'entry': []}).encode()
        response = self.client.post(
            reverse('whatsapp_webhook'), body, content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=self.signature(body),
        )
        self.assertEqual(response.status_code, 200)

    @override_settings(
        WHATSAPP_CLOUD_MODE='live',
        WHATSAPP_CLOUD_VERIFY_TOKEN=verify_token,
    )
    def test_get_challenge_requires_matching_token_and_safe_challenge(self):
        url = reverse('whatsapp_webhook')
        params = {'hub.mode': 'subscribe', 'hub.verify_token': self.verify_token, 'hub.challenge': 'abc-123'}
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'abc-123')
        params['hub.verify_token'] = 'wrong-token'
        self.assertEqual(self.client.get(url, params).status_code, 403)
        params['hub.verify_token'] = self.verify_token
        params['hub.challenge'] = '<script>'
        self.assertEqual(self.client.get(url, params).status_code, 400)
