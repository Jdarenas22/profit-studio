from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.tests.helpers import ScenarioTestCase
from apps.payments.models import ManualPayment
from apps.payments.storage import PrivateReceiptsUnavailable, UnavailableReceiptsStorage, receipts_storage


class PrivateBucketIsolationTests(SimpleTestCase):
    def test_missing_or_shared_bucket_never_uses_public_media(self):
        for bucket in ('', 'media-publico'):
            with self.subTest(bucket=bucket), override_settings(AWS_STORAGE_BUCKET_NAME='media-publico',
                                                               RECEIPTS_BUCKET_NAME=bucket):
                storage = receipts_storage()
                self.assertIsInstance(storage, UnavailableReceiptsStorage)
                for operation in (lambda: storage.save('receipts/test.pdf', ContentFile(b'%PDF-test')),
                                  lambda: storage.open('receipts/test.pdf'),
                                  lambda: storage.url('receipts/test.pdf')):
                    with self.assertRaises(PrivateReceiptsUnavailable):
                        operation()


class UnavailableReceiptUploadTests(ScenarioTestCase):
    def test_unavailable_private_bucket_is_a_form_error_and_does_not_save_payment(self):
        self.login(self.trainer_a)
        before = ManualPayment.objects.count()
        field = ManualPayment._meta.get_field('receipt')
        with mock.patch.object(field, 'storage', UnavailableReceiptsStorage()):
            response = self.client.post(reverse('trainer_manual_payment_add', args=[self.client_a.pk]), {
                'amount': '100000', 'method': 'cash', 'payment_date': timezone.localdate().isoformat(),
                'plan': self.plan.pk, 'notes': '',
                'receipt': SimpleUploadedFile('test.pdf', b'%PDF-1.4\nFictional test only'),
            })
        self.assertEqual(response.status_code, 200)
        self.assertIn('receipt', response.context['errors'])
        self.assertEqual(ManualPayment.objects.count(), before)
