from django.test import TestCase, override_settings
from django.urls import reverse

from apps.payments.models import Payment
from apps.payments.views import MAX_WEBHOOK_BYTES


@override_settings(WOMPI_EVENTS_SECRET='synthetic-webhook-secret')
class WompiBoundsTests(TestCase):
    def test_deep_json_is_rejected_without_payment_changes(self):
        before = Payment.objects.count()
        response = self.client.post(reverse('payment_webhook'), '[' * 2000 + '0' + ']' * 2000,
                                    content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Payment.objects.count(), before)

    def test_oversized_body_returns_413_even_without_declared_length(self):
        url = reverse('payment_webhook')
        response = self.client.post(url, 'x' * (MAX_WEBHOOK_BYTES + 1), content_type='application/json')
        self.assertEqual(response.status_code, 413)
        from django.test import RequestFactory
        from apps.payments.views import payment_webhook
        request = RequestFactory().post(url, 'x' * (MAX_WEBHOOK_BYTES + 1), content_type='application/json')
        request.META.pop('CONTENT_LENGTH', None)
        self.assertEqual(payment_webhook(request).status_code, 413)

    def test_invalid_encoding_and_non_object_are_rejected(self):
        for raw in (b'\xff', b'[]', b'null'):
            with self.subTest(raw=raw):
                self.assertEqual(self.client.post(reverse('payment_webhook'), raw,
                                                 content_type='application/json').status_code, 400)
