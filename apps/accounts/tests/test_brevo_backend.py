"""Backend de correo por la API de Brevo (config/email_backends.py). Sin red: `urlopen` está simulado."""
import json
from io import BytesIO
from unittest import mock
from urllib import error

from django.core.mail import EmailMultiAlternatives, send_mail
from django.test import SimpleTestCase, override_settings

from config.email_backends import BrevoEmailBackend, BrevoError

CLAVE = 'xkeysib-clave-secreta-de-prueba'
SETTINGS = dict(EMAIL_BACKEND='config.email_backends.BrevoEmailBackend', BREVO_API_KEY=CLAVE,
                DEFAULT_FROM_EMAIL='ProFit Studio <negocio@example.com>', EMAIL_TIMEOUT=7)


class FakeResponse:
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@override_settings(**SETTINGS)
class BrevoBackendTests(SimpleTestCase):
    def test_sends_json_with_the_key_in_the_header_and_a_timeout(self):
        with mock.patch('config.email_backends.request.urlopen', return_value=FakeResponse()) as urlopen:
            sent = send_mail('Asunto', 'Cuerpo', None, ['clienta@example.com'])
        self.assertEqual(sent, 1)
        req = urlopen.call_args.args[0]
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 7)
        self.assertEqual(req.full_url, 'https://api.brevo.com/v3/smtp/email')
        self.assertEqual(req.get_header('Api-key'), CLAVE)
        self.assertNotIn(CLAVE, req.full_url)
        body = json.loads(req.data)
        self.assertEqual(body['sender'], {'email': 'negocio@example.com', 'name': 'ProFit Studio'})
        self.assertEqual(body['to'], [{'email': 'clienta@example.com'}])
        self.assertEqual(body['subject'], 'Asunto')
        self.assertEqual(body['textContent'], 'Cuerpo')

    def test_html_alternative_is_sent_too(self):
        message = EmailMultiAlternatives('A', 'texto', None, ['x@example.com'])
        message.attach_alternative('<p>hola</p>', 'text/html')
        with mock.patch('config.email_backends.request.urlopen', return_value=FakeResponse()) as urlopen:
            message.send()
        self.assertEqual(json.loads(urlopen.call_args.args[0].data)['htmlContent'], '<p>hola</p>')

    def test_http_error_raises_a_code_only_oserror_without_the_key(self):
        boom = error.HTTPError('https://api.brevo.com/v3/smtp/email', 401, 'no', {}, BytesIO(CLAVE.encode()))
        with mock.patch('config.email_backends.request.urlopen', side_effect=boom):
            with self.assertRaises(OSError) as ctx:
                send_mail('A', 'B', None, ['x@example.com'])
        self.assertEqual(str(ctx.exception), 'brevo_http_401')
        self.assertNotIn(CLAVE, str(ctx.exception))

    def test_timeout_and_unreachable_are_oserrors(self):
        for side_effect, code in ((TimeoutError(), 'brevo_timeout'), (error.URLError('x'), 'brevo_unreachable')):
            with mock.patch('config.email_backends.request.urlopen', side_effect=side_effect):
                with self.assertRaises(BrevoError) as ctx:
                    send_mail('A', 'B', None, ['x@example.com'])
            self.assertEqual(str(ctx.exception), code)

    def test_fail_silently_returns_zero(self):
        with mock.patch('config.email_backends.request.urlopen', side_effect=error.URLError('x')):
            self.assertEqual(send_mail('A', 'B', None, ['x@example.com'], fail_silently=True), 0)

    def test_without_a_key_nothing_is_called(self):
        with override_settings(BREVO_API_KEY=''), mock.patch('config.email_backends.request.urlopen') as urlopen:
            with self.assertRaises(BrevoError):
                send_mail('A', 'B', None, ['x@example.com'])
        urlopen.assert_not_called()

    def test_verify_reads_transactional_scope_and_sends_nothing(self):
        with mock.patch('config.email_backends.request.urlopen', return_value=FakeResponse()) as urlopen:
            BrevoEmailBackend().verify()
        self.assertEqual(urlopen.call_args.args[0].full_url, 'https://api.brevo.com/v3/smtp/blockedContacts?limit=1')
        self.assertEqual(urlopen.call_args.args[0].get_method(), 'GET')

    def test_the_invitation_helper_survives_a_brevo_failure(self):
        """`email_verification._send` captura OSError: un fallo de Brevo no rompe la petición."""
        from apps.accounts import email_verification as ev
        user = mock.Mock(email='x@example.com', pk=1)
        with mock.patch('config.email_backends.request.urlopen', side_effect=error.URLError('x')), \
                mock.patch.object(ev, 'render_to_string', return_value='texto'):
            self.assertFalse(ev._send(mock.Mock(build_absolute_uri=lambda p: 'http://x' + p), user,
                                      ev.PURPOSE_INVITE, 'http://x/y', ev.PURPOSE_INVITE))


class CheckInfraBrevoTests(SimpleTestCase):
    def _ctx(self, smtp, factory=None, key=CLAVE):
        from types import SimpleNamespace
        settings_ = SimpleNamespace(EMAIL_BACKEND='config.email_backends.BrevoEmailBackend', BREVO_API_KEY=key,
                                    DEFAULT_FROM_EMAIL='ProFit Studio <negocio@example.com>')
        return SimpleNamespace(settings=settings_, smtp=smtp, is_production=True, mail_connection_factory=factory)

    def test_ok_without_probing_and_error_without_key(self):
        from apps.public.management.commands.check_infra import ERROR, OK, check_email
        self.assertEqual(check_email(self._ctx(False))[0].status, OK)
        self.assertEqual(check_email(self._ctx(False, key=''))[0].status, ERROR)

    def test_probe_reports_the_code_and_never_the_key(self):
        from apps.public.management.commands.check_infra import ERROR, OK, check_email

        class Bad:
            def verify(self):
                raise BrevoError('brevo_http_401')

        class Good:
            def verify(self):
                return None
        bad = check_email(self._ctx(True, lambda: Bad()))[0]
        self.assertEqual(bad.status, ERROR)
        self.assertIn('brevo_http_401', bad.detail)
        self.assertNotIn(CLAVE, bad.detail)
        self.assertEqual(check_email(self._ctx(True, lambda: Good()))[0].status, OK)
