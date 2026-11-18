"""Reglas de config/security.py y comprobaciones profit.W001 / profit.W002 (config/checks.py).

Solo claves y nombres de bucket ficticios: nunca se lee el .env ni la configuración real.
"""
from django.core.checks.security import base as django_security
from django.test import SimpleTestCase, override_settings

from config import checks, security

STRONG = 'Prueba-Ficticia-7Hq2!Lx9#Rm4$Tz8&Wc1^Nb6*Pv3_Gs5+Dk0=Yf2'
WEAK_SHORT = 'Clave-Corta-Pero-Variada-4821'
WEAK_REPEATED = 'abcd' * 15


class SecretKeyRulesTests(SimpleTestCase):
    def test_same_thresholds_as_django_w009(self):
        self.assertEqual(security.SECRET_KEY_MIN_LENGTH, django_security.SECRET_KEY_MIN_LENGTH)
        self.assertEqual(security.SECRET_KEY_MIN_UNIQUE_CHARACTERS,
                         django_security.SECRET_KEY_MIN_UNIQUE_CHARACTERS)
        self.assertEqual(security.SECRET_KEY_INSECURE_PREFIX, django_security.SECRET_KEY_INSECURE_PREFIX)

    def test_agrees_with_django_for_every_sample(self):
        samples = (STRONG, WEAK_SHORT, WEAK_REPEATED, '', 'x' * 80, 'django-insecure-' + STRONG,
                   'a1b2c' * 10, 'a1b2' * 13)
        for value in samples:
            with self.subTest(value=value):
                django_ok = django_security._check_secret_key(value)
                self.assertEqual(security.secret_key_weaknesses(value) == [], django_ok)

    def test_reasons_never_include_the_value_or_its_length(self):
        for value in (WEAK_SHORT, WEAK_REPEATED):
            text = ' '.join(security.secret_key_weaknesses(value))
            self.assertNotIn(value, text)
            self.assertNotIn(str(len(value)), text)

    def test_specific_reasons(self):
        self.assertEqual(security.secret_key_weaknesses(STRONG), [])
        self.assertIn('tiene menos de 50 caracteres', security.secret_key_weaknesses(WEAK_SHORT))
        self.assertEqual(security.secret_key_weaknesses(WEAK_REPEATED),
                         ['tiene menos de 5 caracteres distintos'])
        reasons = security.secret_key_weaknesses('django-insecure-' + STRONG)
        self.assertIn('empieza por "django-insecure-"', reasons)
        self.assertIn('es un valor de ejemplo o de desarrollo', reasons)

    def test_placeholders(self):
        for value in ('', '   ', None, 'django-insecure-dev-key-change-in-production',
                      'cambia-esta-clave-secreta-en-produccion', 'ChangeMe' + STRONG, 'tu-clave-larga'):
            with self.subTest(value=value):
                self.assertTrue(security.secret_key_is_placeholder(value))
        self.assertFalse(security.secret_key_is_placeholder(STRONG))
        self.assertFalse(security.secret_key_is_placeholder(WEAK_SHORT))   # débil pero no de ejemplo


class StrictFlagTests(SimpleTestCase):
    def test_true_and_false_values(self):
        for raw in ('true', 'TRUE', ' True ', '1', 'yes', 'on', 'si', 'sí'):
            with self.subTest(raw=raw):
                self.assertTrue(security.parse_strict_flag(raw))
        for raw in ('', None, 'false', 'False', '0', 'no', 'off'):
            with self.subTest(raw=raw):
                self.assertFalse(security.parse_strict_flag(raw))

    def test_unknown_value_is_an_error_that_names_the_variable(self):
        with self.assertRaisesMessage(ValueError, 'SECURITY_STRICT_SECRETS'):
            security.parse_strict_flag('quizas')


class ReceiptsBucketRuleTests(SimpleTestCase):
    def test_missing_same_and_valid(self):
        self.assertEqual(security.receipts_bucket_problem('media', ''), 'missing')
        self.assertEqual(security.receipts_bucket_problem('media', '   '), 'missing')
        self.assertEqual(security.receipts_bucket_problem('media', None), 'missing')
        self.assertEqual(security.receipts_bucket_problem('media', 'media'), 'same')
        self.assertEqual(security.receipts_bucket_problem('media', ' media '), 'same')
        self.assertIsNone(security.receipts_bucket_problem('media', 'recibos-privado'))


class ProjectDeployChecksTests(SimpleTestCase):
    def ids(self, messages):
        return [m.id for m in messages]

    @override_settings(SECRET_KEY=STRONG)
    def test_strong_secret_has_no_w001(self):
        self.assertEqual(checks.check_secret_key_strength(None), [])

    def test_weak_secret_is_a_warning_without_the_value(self):
        for value in (WEAK_SHORT, WEAK_REPEATED):
            with self.subTest(value=value), override_settings(SECRET_KEY=value):
                messages = checks.check_secret_key_strength(None)
                self.assertEqual(self.ids(messages), ['profit.W001'])
                self.assertFalse(messages[0].is_serious())          # aviso, no error
                self.assertNotIn(value, messages[0].msg + (messages[0].hint or ''))

    def test_registered_only_as_deploy_check(self):
        from django.core.checks import registry
        deploy = registry.registry.get_checks(include_deployment_checks=True)
        normal = registry.registry.get_checks(include_deployment_checks=False)
        for check in (checks.check_secret_key_strength, checks.check_receipts_bucket):
            self.assertIn(check, deploy)
            self.assertNotIn(check, normal)       # no aparece en runserver, migrate ni pruebas

    @override_settings(AWS_STORAGE_BUCKET_NAME='')
    def test_without_r2_there_is_no_w002(self):
        self.assertEqual(checks.check_receipts_bucket(None), [])

    def test_r2_without_separate_receipts_bucket_is_w002(self):
        for receipts in ('', 'media-publico'):
            with self.subTest(receipts=receipts), override_settings(
                    AWS_STORAGE_BUCKET_NAME='media-publico', RECEIPTS_BUCKET_NAME=receipts):
                messages = checks.check_receipts_bucket(None)
                self.assertEqual(self.ids(messages), ['profit.W002'])
                self.assertFalse(messages[0].is_serious())
                self.assertIn('R2_RECEIPTS_BUCKET_NAME', messages[0].msg)

    @override_settings(AWS_STORAGE_BUCKET_NAME='media-publico', RECEIPTS_BUCKET_NAME='recibos-privado')
    def test_r2_with_private_receipts_bucket_has_no_w002(self):
        self.assertEqual(checks.check_receipts_bucket(None), [])
