"""Pago manual: formulario (monto, fecha, método, plan, comprobante) y entrega protegida de comprobantes."""
import io
import os
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from PIL import Image

from apps.accounts.tests.helpers import ScenarioTestCase
from apps.memberships.models import MembershipPlan
from apps.payments import views
from apps.payments.forms import MAX_RECEIPT_BYTES, ManualPaymentForm, validate_receipt_file
from apps.payments.models import ManualPayment
from django import forms


def image_bytes(fmt='PNG', size=(20, 20)):
    buf = io.BytesIO()
    Image.new('RGB', size, (200, 30, 30)).save(buf, format=fmt)
    return buf.getvalue()


def upload(name, content, content_type='application/octet-stream'):
    return SimpleUploadedFile(name, content, content_type=content_type)


PDF_BYTES = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'


class ReceiptValidatorTests(SimpleTestCase):
    def assertRejected(self, up):
        with self.assertRaises(forms.ValidationError):
            validate_receipt_file(up)

    def test_valid_files_accepted(self):
        for name, content in (
            ('a.png', image_bytes('PNG')), ('a.jpg', image_bytes('JPEG')),
            ('a.JPEG', image_bytes('JPEG')), ('a.webp', image_bytes('WEBP')), ('a.pdf', PDF_BYTES),
        ):
            up = validate_receipt_file(upload(name, content))
            self.assertEqual(up.tell(), 0, name)   # queda listo para guardarse

    def test_too_big_rejected(self):
        self.assertRejected(upload('a.pdf', b'%PDF-' + b'0' * MAX_RECEIPT_BYTES))

    def test_exactly_max_size_accepted(self):
        content = b'%PDF-' + b'0' * (MAX_RECEIPT_BYTES - 5)
        validate_receipt_file(upload('a.pdf', content))

    def test_disallowed_extensions_rejected(self):
        for name in ('virus.exe', 'x.svg', 'x.html', 'x.gif', 'sin_extension', 'x.php.txt', 'x.png.exe'):
            self.assertRejected(upload(name, image_bytes('PNG')))

    def test_text_disguised_as_image_rejected(self):
        self.assertRejected(upload('a.jpg', b'<?php echo 1; ?>'))
        self.assertRejected(upload('a.png', b'<script>alert(1)</script>'))

    def test_content_not_matching_extension_rejected(self):
        self.assertRejected(upload('a.png', image_bytes('JPEG')))
        self.assertRejected(upload('a.jpg', PDF_BYTES))
        self.assertRejected(upload('a.pdf', image_bytes('PNG')))

    def test_truncated_image_rejected(self):
        self.assertRejected(upload('a.png', image_bytes('PNG')[:40]))

    def test_fake_pdf_rejected(self):
        self.assertRejected(upload('a.pdf', b'hola, esto no es un pdf'))

    def test_huge_dimensions_rejected(self):
        buf = io.BytesIO()
        Image.new('1', (7000, 7000)).save(buf, format='PNG')
        self.assertRejected(upload('a.png', buf.getvalue()))


class ManualPaymentFormTests(TestCase):
    def setUp(self):
        self.plan = MembershipPlan.objects.create(name='Mensual', duration_days=30, reference_price=100000)
        self.today = timezone.localdate()

    def data(self, **overrides):
        data = {'amount': '150000', 'payment_date': self.today.isoformat(), 'method': 'cash',
                'plan': '', 'notes': ''}
        data.update(overrides)
        return data

    def form(self, files=None, **overrides):
        return ManualPaymentForm(self.data(**overrides), files or {})

    def test_valid(self):
        form = self.form(plan=str(self.plan.pk), notes='  pago de mayo ')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['amount'], 150000)
        self.assertEqual(form.cleaned_data['plan'], self.plan)
        self.assertEqual(form.cleaned_data['notes'], 'pago de mayo')

    def test_amount_formats(self):
        for raw, expected in (('150000', 150000), ('150.000', 150000), ('150,000', 150000),
                              ('1.500.000', 1500000), ('$ 80000', 80000), (' 5 ', 5)):
            form = self.form(amount=raw)
            self.assertTrue(form.is_valid(), (raw, form.errors))
            self.assertEqual(form.cleaned_data['amount'], expected, raw)

    def test_invalid_amounts(self):
        for raw in ('', '0', '000', '-5', 'abc', '1.5', '1500,50', '12e3', '1..000', '100000001',
                    '99999999999999999999', '１２３', '15 000'):
            form = self.form(amount=raw)
            self.assertFalse(form.is_valid(), raw)
            self.assertIn('amount', form.errors, raw)

    def test_invalid_dates(self):
        for raw in ('', 'ayer', '2026-13-45', (self.today + timedelta(days=1)).isoformat(), '2001-01-01'):
            form = self.form(payment_date=raw)
            self.assertFalse(form.is_valid(), raw)
            self.assertIn('payment_date', form.errors, raw)

    def test_invalid_method(self):
        for raw in ('', 'bitcoin', "cash'; DROP TABLE x;--"):
            form = self.form(method=raw)
            self.assertFalse(form.is_valid(), raw)
            self.assertIn('method', form.errors, raw)

    def test_inactive_or_unknown_plan_rejected(self):
        inactive = MembershipPlan.objects.create(name='Viejo', duration_days=10, reference_price=1,
                                                 is_active=False)
        for raw in (str(inactive.pk), '99999', 'x'):
            form = self.form(plan=raw)
            self.assertFalse(form.is_valid(), raw)
            self.assertIn('plan', form.errors, raw)

    def test_notes_too_long(self):
        self.assertIn('notes', self.form(notes='x' * 1001).errors)

    def test_receipt_errors_reach_form(self):
        form = self.form(files={'receipt': upload('a.exe', b'MZ')})
        self.assertFalse(form.is_valid())
        self.assertIn('receipt', form.errors)

    def test_empty_receipt_file_rejected(self):
        form = self.form(files={'receipt': upload('a.pdf', b'')})
        self.assertIn('receipt', form.errors)

    def test_errors_by_field_are_plain_strings(self):
        form = self.form(amount='0', payment_date='')
        form.is_valid()
        errors = form.error_messages_by_field()
        self.assertIsInstance(errors['amount'], str)
        self.assertEqual(errors['payment_date'], 'La fecha es obligatoria.')


class ManualPaymentViewTests(ScenarioTestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.today = timezone.localdate().isoformat()

    def url(self, client):
        return reverse('trainer_manual_payment_add', args=[client.pk])

    def post(self, client, **overrides):
        data = {'amount': '150000', 'payment_date': self.today, 'method': 'transfer',
                'plan': str(self.plan.pk), 'notes': 'ok'}
        data.update(overrides)
        return self.client.post(self.url(client), data)

    def test_valid_payment_is_saved(self):
        self.login(self.trainer_a)
        before = ManualPayment.objects.count()
        response = self.post(self.client_a)
        self.assertRedirects(response, reverse('trainer_client_detail', args=[self.client_a.pk]),
                             fetch_redirect_response=False)
        self.assertEqual(ManualPayment.objects.count(), before + 1)
        mp = ManualPayment.objects.latest('id')
        self.assertEqual((mp.amount, mp.method, mp.user, mp.trainer, mp.plan),
                         (150000, 'transfer', self.client_a, self.trainer_a, self.plan))

    def test_invalid_amount_shows_error_with_template_keys(self):
        self.login(self.trainer_a)
        before = ManualPayment.objects.count()
        response = self.post(self.client_a, amount='0')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ManualPayment.objects.count(), before)
        self.assertIn('amount', response.context['errors'])
        self.assertContains(response, 'El monto debe ser mayor que cero.')
        self.assertEqual(response.context['form']['amount'], '0')   # se conserva lo escrito

    def test_missing_fields_show_template_errors(self):
        self.login(self.trainer_a)
        response = self.client.post(self.url(self.client_a), {})
        self.assertContains(response, 'El monto es obligatorio.')
        self.assertContains(response, 'La fecha es obligatoria.')

    def test_future_date_rejected(self):
        self.login(self.trainer_a)
        future = (timezone.localdate() + timedelta(days=3)).isoformat()
        before = ManualPayment.objects.count()
        response = self.post(self.client_a, payment_date=future)
        self.assertIn('payment_date', response.context['errors'])
        self.assertEqual(ManualPayment.objects.count(), before)

    def _bad_receipt_post(self):
        return self.client.post(self.url(self.client_a), {
            'amount': '1000', 'payment_date': self.today, 'method': 'cash',
            'receipt': upload('a.png', b'no soy una imagen'),
        })

    def test_receipt_error_is_painted_inline_and_not_flashed_again(self):
        # manual_payment_add.html pinta `errors.receipt` junto al campo
        self.login(self.trainer_a)
        response = self._bad_receipt_post()
        self.assertIn('receipt', response.context['errors'])
        self.assertContains(response, escape(response.context['errors']['receipt']))
        self.assertFalse([str(m) for m in response.context['messages']])
        self.assertNotContains(response, 'Comprobante: ')

    def test_inline_fields_are_never_flashed(self):
        self.login(self.trainer_a)
        for field, over in (('amount', {'amount': ''}), ('payment_date', {'payment_date': ''}),
                            ('method', {'method': 'bitcoin'}), ('notes', {'notes': 'x' * 1001})):
            with self.subTest(field=field):
                data = {'amount': '1000', 'payment_date': self.today, 'method': 'cash'}
                data.update(over)
                response = self.client.post(self.url(self.client_a), data)
                self.assertIn(field, response.context['errors'])
                self.assertFalse([str(m) for m in response.context['messages']])

    def test_error_not_painted_by_the_template_is_flashed(self):
        self.login(self.trainer_a)
        inline = tuple(f for f in views._FIELDS_WITH_INLINE_ERROR if f != 'receipt')
        with mock.patch.object(views, '_FIELDS_WITH_INLINE_ERROR', inline):
            response = self._bad_receipt_post()
        self.assertContains(response, 'Comprobante: ')
        self.assertTrue([str(m) for m in response.context['messages']])

    def test_receipt_saved_with_random_name(self):
        self.login(self.trainer_a)
        response = self.client.post(self.url(self.client_a), {
            'amount': '1000', 'payment_date': self.today, 'method': 'cash',
            'receipt': upload('mi comprobante personal.PNG', image_bytes('PNG')),
        })
        self.assertEqual(response.status_code, 302)
        mp = ManualPayment.objects.latest('id')
        self.assertRegex(mp.receipt.name, r'^receipts/[0-9a-f]{32}\.png$')
        self.assertNotIn('personal', mp.receipt.name)

    def test_trainer_cannot_register_payment_for_foreign_client(self):
        self.login(self.trainer_a)
        before = ManualPayment.objects.count()
        self.assertEqual(self.post(self.client_b).status_code, 404)
        self.assertEqual(ManualPayment.objects.count(), before)

    def test_member_cannot_register_payments(self):
        self.login(self.client_a)
        self.assertEqual(self.post(self.client_a).status_code, 403)

    def test_sql_like_text_is_just_text(self):
        self.login(self.trainer_a)
        self.post(self.client_a, notes="'; DROP TABLE payments_manualpayment; --")
        self.assertEqual(ManualPayment.objects.latest('id').notes, "'; DROP TABLE payments_manualpayment; --")


class ReceiptDownloadTests(ScenarioTestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.mp = ManualPayment.objects.create(
            user=self.client_a, trainer=self.trainer_a, amount=1000, payment_date=timezone.localdate(),
            receipt=upload('c.png', image_bytes('PNG')),
        )
        self.url = reverse('payment_receipt', args=[self.mp.pk])

    def test_owner_trainer_and_boss_can_download(self):
        for user in (self.client_a, self.trainer_a, self.boss):
            self.client.logout()
            self.login(user)
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200, user.username)
            self.assertEqual(response['Content-Type'], 'image/png')
            self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
            self.assertIn('no-store', response['Cache-Control'])
            self.assertEqual(b''.join(response.streaming_content), image_bytes('PNG'))

    def test_other_trainer_and_other_client_get_404(self):
        for user in (self.trainer_b, self.client_b, self.client_free):
            self.client.logout()
            self.login(user)
            self.assertEqual(self.client.get(self.url).status_code, 404, user.username)

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'])

    def test_payment_without_receipt_is_404(self):
        self.login(self.boss)
        mp = ManualPayment.objects.create(
            user=self.client_a, trainer=self.trainer_a, amount=5, payment_date=timezone.localdate(),
        )
        self.assertEqual(self.client.get(reverse('payment_receipt', args=[mp.pk])).status_code, 404)

    def test_missing_file_is_404_not_500(self):
        self.login(self.boss)
        self.mp.receipt.storage.delete(self.mp.receipt.name)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_post_not_allowed(self):
        self.login(self.boss)
        self.assertEqual(self.client.post(self.url).status_code, 405)


# ─── Comprobantes: sin acceso público directo ──────────────────────────────────

class ReceiptsNotPublicTests(ScenarioTestCase):
    """/media/receipts/... responde 404 aunque el archivo exista; la vista protegida sí lo entrega."""

    def setUp(self):
        import importlib
        import config.urls
        from django.urls import clear_url_caches

        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        for sub, name in (('receipts', 'x.png'), ('profiles', 'p.png')):
            os.makedirs(os.path.join(self.media, sub), exist_ok=True)
            with open(os.path.join(self.media, sub, name), 'wb') as fh:
                fh.write(image_bytes('PNG'))

        def reload_urls(debug):
            with override_settings(DEBUG=debug, MEDIA_ROOT=self.media):
                importlib.reload(config.urls)
            clear_url_caches()

        self.reload_urls = reload_urls
        self.addCleanup(lambda: (importlib.reload(config.urls), clear_url_caches()))

    def _check_public_route(self, debug):
        self.reload_urls(debug)
        with override_settings(DEBUG=debug, MEDIA_ROOT=self.media):
            # El resto de media sigue sirviéndose (el bloqueo es solo para receipts/)
            ok = self.client.get('/media/profiles/p.png')
            self.assertEqual(ok.status_code, 200, 'la media pública debe seguir funcionando')
            ok.close()
            for url in ('/media/receipts/x.png', '/media/receipts/', '/media/receipts',
                        '/media//receipts/x.png', '/media/./receipts/x.png',
                        '/media/profiles/../receipts/x.png', '/media/RECEIPTS/x.png',
                        '/media/receipts./x.png', '/media/receipts%2Fx.png',
                        '/media/receipts/no-existe.png', '/media/%72eceipts/x.png'):
                with self.subTest(url=url):
                    self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_public_media_route_blocks_receipts_in_production_without_r2(self):
        self._check_public_route(debug=False)

    def test_public_media_route_blocks_receipts_in_debug_too(self):
        self._check_public_route(debug=True)

    def test_protected_view_still_serves_the_same_file(self):
        self.reload_urls(False)
        with override_settings(DEBUG=False, MEDIA_ROOT=self.media):
            mp = ManualPayment.objects.create(
                user=self.client_a, trainer=self.trainer_a, amount=1000,
                payment_date=timezone.localdate(), receipt=upload('c.png', image_bytes('PNG')),
            )
            public_url = '/media/' + mp.receipt.name          # lo que antes era público
            self.assertTrue(mp.receipt.name.startswith('receipts/'))
            for user in (self.client_a, self.trainer_a, self.boss):
                self.client.logout()
                self.login(user)
                response = self.client.get(reverse('payment_receipt', args=[mp.pk]))
                self.assertEqual(response.status_code, 200, user.username)
                self.assertEqual(b''.join(response.streaming_content), image_bytes('PNG'))
                self.assertEqual(self.client.get(public_url).status_code, 404, user.username)
            self.client.logout()
            self.assertEqual(self.client.get(public_url).status_code, 404)   # anónimo tampoco


class PrivateMediaPathTests(SimpleTestCase):
    def test_paths_under_receipts_are_private_in_every_spelling(self):
        from config.media import is_private_media_path
        for path in ('receipts/x.png', 'receipts', '/receipts/x.png', './receipts/x.png',
                     'a/../receipts/x.png', 'RECEIPTS/x.png', 'Receipts/sub/x.png', 'receipts./x.png',
                     'receipts /x.png', 'receipts\\x.png', '../x', '..'):
            with self.subTest(path=path):
                self.assertTrue(is_private_media_path(path), path)

    def test_other_paths_are_public(self):
        from config.media import is_private_media_path
        for path in ('profiles/p.png', 'exercises/images/a.jpg', 'my-receipts/x.png',
                     'receipts-old/x.png', 'profiles/receipts/x.png', 'receiptsx', '', '.'):
            with self.subTest(path=path):
                self.assertFalse(is_private_media_path(path), path)


class ReceiptsStorageTests(SimpleTestCase):
    """Con R2 los comprobantes usan un storage privado; sin R2, el de siempre."""

    R2 = dict(
        AWS_ACCESS_KEY_ID='k', AWS_SECRET_ACCESS_KEY='s', AWS_STORAGE_BUCKET_NAME='media-publico',
        AWS_S3_ENDPOINT_URL='https://acc123.r2.cloudflarestorage.com', AWS_DEFAULT_ACL=None,
        AWS_QUERYSTRING_AUTH=False, AWS_S3_REGION_NAME='auto', AWS_S3_SIGNATURE_VERSION='s3v4',
        AWS_S3_CUSTOM_DOMAIN='pub-xxxx.r2.dev',   # dominio público del bucket de media
    )

    def test_without_r2_uses_the_default_storage(self):
        from django.core.files.storage import default_storage
        from apps.payments.storage import receipts_storage
        with override_settings(AWS_STORAGE_BUCKET_NAME=''):
            self.assertIs(receipts_storage(), default_storage)

    def test_model_field_uses_the_receipts_storage_factory(self):
        from apps.payments.storage import receipts_storage
        field = ManualPayment._meta.get_field('receipt')
        self.assertIs(field._storage_callable, receipts_storage)

    def test_with_r2_receipts_use_private_signed_storage_on_their_own_bucket(self):
        from apps.payments.storage import receipts_storage
        with override_settings(RECEIPTS_BUCKET_NAME='recibos-privado', **self.R2):
            storage = receipts_storage()
            self.assertEqual(storage.bucket_name, 'recibos-privado')
            self.assertIsNone(storage.custom_domain)      # no hereda el dominio público de media
            self.assertTrue(storage.querystring_auth)
            self.assertEqual(storage.querystring_expire, 300)
            url = storage.url('receipts/abc.png')         # firma local: no hace llamadas de red
            self.assertNotIn('pub-xxxx.r2.dev', url)
            self.assertIn('recibos-privado/receipts/abc.png', url)
            self.assertIn('X-Amz-Signature=', url)        # SigV4 (la única que acepta R2)
            self.assertIn('X-Amz-Expires=300', url)

    def test_with_r2_and_no_receipts_bucket_falls_back_to_media_bucket_but_stays_signed(self):
        from apps.payments.storage import receipts_storage
        with override_settings(RECEIPTS_BUCKET_NAME='', **self.R2):
            storage = receipts_storage()
            self.assertEqual(storage.bucket_name, 'media-publico')
            self.assertIsNone(storage.custom_domain)
            self.assertIn('X-Amz-Signature=', storage.url('receipts/abc.png'))
