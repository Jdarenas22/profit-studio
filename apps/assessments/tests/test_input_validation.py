"""Valoraciones, test Ruffier-Dickson y mediciones: entradas inválidas no deben dar 500."""
from decimal import Decimal
from unittest import mock

from django.urls import reverse
from django.utils.html import escape

from apps.accounts.tests.helpers import ScenarioTestCase
from apps.assessments import views
from apps.assessments.forms import BodyMeasurementForm, InitialAssessmentForm
from apps.assessments.models import BodyMeasurement, DixonTest, InitialAssessment


def messages_of(response):
    return [str(m) for m in response.context['messages']]


class AssessmentCreateTests(ScenarioTestCase):
    def setUp(self):
        self.login(self.trainer_a)
        self.url = reverse('trainer_assessment_create', args=[self.client_a.pk])
        self.count = InitialAssessment.objects.count()

    def payload(self, **over):
        data = {'age': '28', 'sex': 'F', 'weight': '72.5', 'height': '1.68', 'goal': 'Tonificar'}
        data.update(over)
        return data

    def assert_rejected(self, field, **over):
        response = self.client.post(self.url, self.payload(**over))
        self.assertEqual(response.status_code, 200, over)
        self.assertIn(field, response.context['errors'], over)
        self.assertEqual(InitialAssessment.objects.count(), self.count, over)
        # assessment_create.html pinta el error junto al campo: no se repite como flash
        self.assertFalse(messages_of(response), 'error pintado inline: no debe duplicarse como flash')
        self.assertContains(response, escape(response.context['errors'][field]))
        return response

    def test_error_not_painted_by_the_template_is_flashed(self):
        rendered = tuple(f for f in views.ASSESSMENT_RENDERED if f != 'age')
        with mock.patch.object(views, 'ASSESSMENT_RENDERED', rendered):
            response = self.client.post(self.url, self.payload(age='x'))
        self.assertIn('age', response.context['errors'])
        self.assertTrue(any('edad' in m.lower() for m in messages_of(response)))

    def test_valid_assessment_in_meters(self):
        response = self.client.post(self.url, self.payload())
        a = InitialAssessment.objects.latest('pk')
        self.assertRedirects(response, reverse('trainer_assessment_detail', args=[a.pk]))
        self.assertEqual((a.height, a.weight, a.imc), (Decimal('1.68'), Decimal('72.50'), Decimal('25.69')))
        self.assertFalse(hasattr(a, 'dixon_test'))

    def test_height_in_centimeters_is_converted(self):
        """168 (cm) antes desbordaba DecimalField(max_digits=4): ahora se guarda como 1,68 m."""
        self.client.post(self.url, self.payload(height='168'))
        self.assertEqual(InitialAssessment.objects.latest('pk').height, Decimal('1.68'))

    def test_height_with_decimal_comma(self):
        self.client.post(self.url, self.payload(height='1,68', weight='72,5'))
        a = InitialAssessment.objects.latest('pk')
        self.assertEqual((a.height, a.weight), (Decimal('1.68'), Decimal('72.50')))

    def test_height_zero_is_rejected_without_zero_division(self):
        for bad in ('0', '0.0', '-1.7', '0.49', '2.6', '3', '3.5', '49', '251', '1000', 'abc', '', 'NaN',
                    'Infinity', '1e400'):
            with self.subTest(height=bad):
                self.assert_rejected('height', height=bad)

    def test_weight_limits_and_garbage(self):
        for bad in ('', 'abc', '-5', '0', '19.9', '400.1', '99999999999999999999', 'NaN', '1e400'):
            with self.subTest(weight=bad):
                self.assert_rejected('weight', weight=bad)
        self.client.post(self.url, self.payload(weight='400', height='2.5'))
        self.assertEqual(InitialAssessment.objects.latest('pk').weight, Decimal('400.00'))

    def test_weight_and_height_must_be_coherent_so_imc_fits_its_column(self):
        # 400 kg con 0,5 m daría IMC 1600: desbordaría DecimalField(5,2) en PostgreSQL
        self.assert_rejected('height', weight='400', height='0.5')
        self.assert_rejected('height', weight='20', height='2.5')

    def test_age_sex_goal_and_text_limits(self):
        for bad in ('', 'x', '4', '101', '-3', '30.5', '99999999999999999999'):
            with self.subTest(age=bad):
                self.assert_rejected('age', age=bad)
        self.assert_rejected('sex', sex='Z')
        self.assert_rejected('sex', sex='')
        self.assert_rejected('goal', goal='')
        self.assert_rejected('goal', goal='x' * 1001)
        self.assert_rejected('physical_restrictions', physical_restrictions='x' * 2001)
        self.assert_rejected('observations', observations='x' * 2001)

    def test_missing_fields_do_not_raise_key_error(self):
        response = self.client.post(self.url, {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.context['errors']), {'age', 'sex', 'weight', 'height', 'goal'})

    def test_dixon_with_the_three_pulses_is_created(self):
        self.client.post(self.url, self.payload(p0='70', p1='150', p2='90', dixon_observations='ok'))
        a = InitialAssessment.objects.latest('pk')
        self.assertEqual((a.dixon_test.p0, a.dixon_test.index_value), (70, Decimal('11.00')))

    def test_dixon_partial_or_out_of_range_pulses_are_rejected(self):
        self.assert_rejected('p0', p0='70', p1='', p2='')           # incompleto
        self.assert_rejected('p1', p0='70', p1='19', p2='80')
        self.assert_rejected('p1', p0='70', p1='251', p2='80')
        self.assert_rejected('p2', p0='70', p1='100', p2='abc')
        self.assert_rejected('p0', p0='-5', p1='100', p2='80')
        self.assertFalse(DixonTest.objects.filter(assessment__user=self.client_a, assessment__goal='Tonificar').exists())

    def test_nothing_is_saved_if_dixon_creation_fails_midway(self):
        """La valoración y el test se guardan juntos (transaction.atomic)."""
        from unittest import mock
        with mock.patch('apps.assessments.views.DixonTest.objects.create', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url, self.payload(p0='70', p1='150', p2='90'))
        self.assertEqual(InitialAssessment.objects.count(), self.count)


class DixonDetailTests(ScenarioTestCase):
    def setUp(self):
        self.login(self.trainer_a)
        self.url = reverse('trainer_assessment_detail', args=[self.assessment_a.pk])

    def test_error_not_painted_by_the_template_is_flashed(self):
        rendered = tuple(f for f in views.DIXON_RENDERED if f != 'dixon_observations')
        with mock.patch.object(views, 'DIXON_RENDERED', rendered):
            response = self.client.post(self.url, {'p0': '70', 'p1': '100', 'p2': '80',
                                                   'dixon_observations': 'x' * 1001})
        self.assertTrue(any('Observaciones del test' in m for m in messages_of(response)))

    def test_valid_pulses_create_and_then_update(self):
        response = self.client.post(self.url, {'p0': '70', 'p1': '100', 'p2': '80'})
        self.assertRedirects(response, self.url)
        self.assertEqual(self.assessment_a.dixon_test.index_value, Decimal('5.00'))
        self.client.post(self.url, {'p0': '60', 'p1': '90', 'p2': '70'})
        self.assertEqual(DixonTest.objects.filter(assessment=self.assessment_a).count(), 1)

    def test_invalid_pulses_do_not_500(self):
        bad_payloads = [
            {}, {'p0': '', 'p1': '', 'p2': ''}, {'p0': 'a', 'p1': '1', 'p2': '2'},
            {'p0': '70', 'p1': '100'}, {'p0': '-1', 'p1': '100', 'p2': '80'},
            {'p0': '70', 'p1': '999', 'p2': '80'}, {'p0': '19', 'p1': '100', 'p2': '80'},
            {'p0': '1' * 5000, 'p1': '100', 'p2': '80'}, {'p0': '70.5', 'p1': '100', 'p2': '80'},
        ]
        for data in bad_payloads:
            with self.subTest(data={k: v[:10] for k, v in data.items()}):
                response = self.client.post(self.url, data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['errors'])
                self.assertFalse(messages_of(response))   # assessment_detail.html los pinta inline
        self.assertFalse(DixonTest.objects.filter(assessment=self.assessment_a).exists())

    def test_observations_are_limited(self):
        response = self.client.post(self.url, {'p0': '70', 'p1': '100', 'p2': '80',
                                               'dixon_observations': 'x' * 1001})
        self.assertIn('dixon_observations', response.context['errors'])


class BodyMeasurementTests(ScenarioTestCase):
    def setUp(self):
        self.login(self.trainer_a)
        self.url = reverse('trainer_measurement_add', args=[self.client_a.pk])
        self.count = BodyMeasurement.objects.count()

    def test_valid_measurement_with_optional_fields(self):
        response = self.client.post(self.url, {'weight': '70,5', 'height': '168', 'waist_cm': '80.25', 'notes': 'ok'})
        self.assertRedirects(response, reverse('trainer_client_detail', args=[self.client_a.pk]))
        m = BodyMeasurement.objects.latest('pk')
        self.assertEqual((m.weight, m.height, m.waist_cm), (Decimal('70.5'), Decimal('1.68'), Decimal('80.3')))
        self.assertEqual(m.imc, Decimal('24.98'))

    def test_only_weight_is_required(self):
        self.client.post(self.url, {'weight': '70'})
        m = BodyMeasurement.objects.latest('pk')
        self.assertIsNone(m.height)
        self.assertIsNone(m.waist_cm)

    def test_invalid_values_are_reported_instead_of_silently_ignored(self):
        cases = [
            ('weight', {'weight': ''}), ('weight', {'weight': 'abc'}), ('weight', {'weight': '-3'}),
            ('weight', {'weight': '19'}), ('weight', {'weight': '401'}), ('weight', {'weight': '1e999'}),
            ('height', {'weight': '70', 'height': '0'}), ('height', {'weight': '70', 'height': 'abc'}),
            ('height', {'weight': '70', 'height': '4'}), ('waist_cm', {'weight': '70', 'waist_cm': 'xx'}),
            ('waist_cm', {'weight': '70', 'waist_cm': '29'}), ('waist_cm', {'weight': '70', 'waist_cm': '301'}),
            ('notes', {'weight': '70', 'notes': 'x' * 2001}),
            ('height', {'weight': '400', 'height': '0.5'}),
        ]
        for field, data in cases:
            with self.subTest(data=data):
                response = self.client.post(self.url, data)
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context['errors'])
                self.assertEqual(response.context['form'].get('weight', ''), data.get('weight', ''))
        self.assertEqual(BodyMeasurement.objects.count(), self.count)

    def test_inline_errors_are_not_flashed_again(self):
        # body_measurement_add.html pinta weight, height, waist_cm y notes
        for data, fields in (({'weight': '70', 'height': '0'}, {'height'}),
                             ({'weight': 'abc', 'waist_cm': '1', 'notes': 'x' * 2001},
                              {'weight', 'waist_cm', 'notes'})):
            with self.subTest(fields=sorted(fields)):
                response = self.client.post(self.url, data)
                self.assertTrue(fields <= set(response.context['errors']))
                self.assertFalse(messages_of(response))

    def test_error_not_painted_by_the_template_is_flashed(self):
        rendered = tuple(f for f in views.MEASUREMENT_RENDERED if f != 'height')
        with mock.patch.object(views, 'MEASUREMENT_RENDERED', rendered):
            response = self.client.post(self.url, {'weight': '70', 'height': '0'})
        self.assertTrue(any('estatura' in m.lower() for m in messages_of(response)))


class FormUnitTests(ScenarioTestCase):
    """Reglas de unidades documentadas: metros 0,5-2,5 y centímetros 50-250 (sin solaparse)."""

    def test_height_unit_boundaries(self):
        def height(raw):
            form = InitialAssessmentForm({'age': '30', 'sex': 'F', 'weight': '70', 'height': raw, 'goal': 'x'})
            return form.cleaned_data.get('height') if form.is_valid() or 'height' not in form.errors else None
        self.assertEqual(height('1.68'), Decimal('1.68'))
        self.assertEqual(height('168'), Decimal('1.68'))
        self.assertEqual(height('180,5'), Decimal('1.81'))     # cm con coma, redondeado
        self.assertEqual(height('250'), Decimal('2.50'))
        self.assertEqual(height('2.5'), Decimal('2.50'))
        for rejected in ('2.51', '2.9', '3', '3.01', '49.9', '0.4', '251'):
            self.assertIsNone(height(rejected), rejected)

    def test_measurement_form_height_is_optional(self):
        form = BodyMeasurementForm({'weight': '70'})
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data['height'])
