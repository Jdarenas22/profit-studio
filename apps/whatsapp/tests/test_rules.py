"""Reglas puras: número, ventana horaria y festivos, clasificación de errores y textos."""
from datetime import date, datetime

from django.test import SimpleTestCase

from apps.whatsapp import calendar as cal
from apps.whatsapp import catalog, phone
from apps.whatsapp.client import base

from .helpers import BOGOTA


class PhoneTests(SimpleTestCase):
    def test_valid_colombian_mobiles(self):
        for raw in ('300 123 4567', '3001234567', '+57 300-123-4567', '0057 300 123 4567', '573001234567',
                    '(300) 123.4567', ' +573001234567 '):
            with self.subTest(raw=raw):
                self.assertEqual(phone.normalize_phone(raw), '+573001234567')

    def test_invalid_numbers(self):
        for raw in ('', '6012345678', '+1 415 555 1234', '30012345', 'tres cero cero', '+57 300 123 456',
                    '+57 6012345678', '+57300123456a'):
            with self.subTest(raw=raw), self.assertRaises(phone.InvalidPhone):
                phone.normalize_phone(raw)

    def test_display_and_mask(self):
        self.assertEqual(phone.display('+573001234567'), '+57 300 123 4567')
        masked = phone.mask('+573001234567')
        self.assertEqual(masked, '+57********67')
        self.assertNotIn('1234', masked)
        self.assertEqual(phone.from_wa_id('573001234567'), '+573001234567')
        self.assertEqual(phone.from_wa_id('abc'), '')


class CalendarTests(SimpleTestCase):
    HOLIDAYS_2026 = {date(2026, 1, 1), date(2026, 1, 12), date(2026, 3, 23), date(2026, 4, 2), date(2026, 4, 3),
                     date(2026, 5, 1), date(2026, 5, 18), date(2026, 6, 8), date(2026, 6, 15), date(2026, 6, 29),
                     date(2026, 7, 20), date(2026, 8, 7), date(2026, 8, 17), date(2026, 10, 12), date(2026, 11, 2),
                     date(2026, 11, 16), date(2026, 12, 8), date(2026, 12, 25)}
    HOLIDAYS_2027 = {date(2027, 1, 1), date(2027, 1, 11), date(2027, 3, 22), date(2027, 3, 25), date(2027, 3, 26),
                     date(2027, 5, 1), date(2027, 5, 10), date(2027, 5, 31), date(2027, 6, 7), date(2027, 7, 5),
                     date(2027, 7, 20), date(2027, 8, 7), date(2027, 8, 16), date(2027, 10, 18), date(2027, 11, 1),
                     date(2027, 11, 15), date(2027, 12, 8), date(2027, 12, 25)}

    def test_official_holidays(self):
        self.assertEqual(cal.easter_sunday(2026), date(2026, 4, 5))
        self.assertEqual(cal.easter_sunday(2027), date(2027, 3, 28))
        self.assertEqual(set(cal.colombian_holidays(2026)), self.HOLIDAYS_2026)
        self.assertEqual(set(cal.colombian_holidays(2027)), self.HOLIDAYS_2027)

    def at(self, *args):
        return datetime(*args, tzinfo=BOGOTA)

    def test_window(self):
        self.assertTrue(cal.in_window(self.at(2026, 11, 17, 7, 0)))       # martes 7:00
        self.assertTrue(cal.in_window(self.at(2026, 11, 17, 18, 59)))
        self.assertFalse(cal.in_window(self.at(2026, 11, 17, 19, 1)))
        self.assertFalse(cal.in_window(self.at(2026, 11, 17, 6, 59)))
        self.assertTrue(cal.in_window(self.at(2026, 11, 21, 8, 0)))       # sábado 8:00
        self.assertFalse(cal.in_window(self.at(2026, 11, 21, 15, 1)))     # sábado 15:01
        self.assertFalse(cal.in_window(self.at(2026, 11, 22, 10, 0)))     # domingo
        self.assertFalse(cal.in_window(self.at(2026, 11, 16, 10, 0)))     # lunes festivo

    def test_next_open(self):
        self.assertEqual(cal.next_open(self.at(2026, 11, 21, 16, 0)), self.at(2026, 11, 23, 7, 0))   # sáb -> lun
        self.assertEqual(cal.next_open(self.at(2026, 11, 15, 12, 0)), self.at(2026, 11, 17, 7, 0))   # dom + festivo
        self.assertEqual(cal.next_open(self.at(2026, 11, 17, 5, 0)), self.at(2026, 11, 17, 7, 0))
        moment = self.at(2026, 11, 17, 10, 0)
        self.assertEqual(cal.next_open(moment), moment)


class ClassifyTests(SimpleTestCase):
    def test_classes(self):
        cases = {131000: base.TRANSIENT, 130429: base.TRANSIENT, 131056: base.TRANSIENT, 131049: base.ECOSYSTEM,
                 131026: base.RECIPIENT, 131047: base.WINDOW, 132001: base.TEMPLATE, 132000: base.TEMPLATE,
                 190: base.ACCOUNT, 250: base.ACCOUNT, 131042: base.ACCOUNT, 999999: base.UNKNOWN}
        for code, expected in cases.items():
            with self.subTest(code=code):
                self.assertEqual(base.classify(code), expected)
        self.assertEqual(base.classify(None, 503), base.TRANSIENT)
        self.assertEqual(base.classify(None, None), base.TRANSIENT)
        self.assertEqual(base.classify(None, 401), base.ACCOUNT)


class CatalogTests(SimpleTestCase):
    def test_names_dates_and_optout(self):
        self.assertEqual(catalog.spanish_date(date(2026, 12, 15)), '15 de diciembre de 2026')
        self.assertEqual(catalog.clean_name('  Ana\nMaría\t ', 'clienta'), 'Ana')
        self.assertEqual(catalog.clean_name('', 'clienta'), 'clienta')
        self.assertEqual(len(catalog.clean_name('x' * 80, 'c')), 30)
        for text in ('BAJA', ' Stop. ', 'no más', 'NO MAS!', 'Parar'):
            with self.subTest(text=text):
                self.assertTrue(catalog.is_optout_text(text))
        for text in ('no puedo parar de entrenar', 'hola', 'baja de peso rápido'):
            with self.subTest(text=text):
                self.assertFalse(catalog.is_optout_text(text))

    def test_templates_never_ask_for_health_fields(self):
        allowed = {'nombre', 'fecha'}
        for spec in catalog.TEMPLATES.values():
            self.assertTrue(set(spec.params) <= allowed, spec.name)
            self.assertTrue(spec.name.endswith('_v1'))
