"""Precios y montos en pesos colombianos: con punto de miles ($120.000), nunca $120000 ni $120,000."""
import re
from datetime import date
from pathlib import Path

from django.conf import settings
from django.db.models import Sum
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import formats

from apps.accounts.models import User
from apps.accounts.tests.helpers import NO_MANIFEST, ScenarioTestCase, make_user
from apps.memberships.models import MembershipPlan
from apps.payments.models import ManualPayment


@NO_MANIFEST
class PlanPricesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        MembershipPlan.objects.create(name='Mensual', duration_days=30, reference_price=120000)
        MembershipPlan.objects.create(name='Semestral', duration_days=180, reference_price=1250000)

    def test_public_plans_page(self):
        html = self.client.get(reverse('plans')).content.decode()
        self.assertIn('$120.000', html)
        self.assertIn('$1.250.000', html)
        self.assertNotRegex(html, r'\$\s*120000\b')

    def test_register_page_lists_the_plans_with_thousands_point(self):
        html = self.client.get(reverse('register')).content.decode()
        self.assertIn('$120.000 COP', html)
        self.assertIn('$1.250.000 COP', html)
        self.assertNotRegex(html, r'\$\s*(120000|1250000)\b')


@NO_MANIFEST
class TrainerMoneyTests(ScenarioTestCase):
    def test_total_collected_and_row_amounts(self):
        ManualPayment.objects.create(user=self.client_a, amount=120000, method='cash', payment_date=date.today(),
                                     trainer=self.boss)
        ManualPayment.objects.create(user=self.client_a, amount=1250000, method='cash', payment_date=date.today(),
                                     trainer=self.boss)
        self.client.force_login(self.boss)
        html = self.client.get(reverse('trainer_manual_payment_list')).content.decode()
        total = ManualPayment.objects.aggregate(t=Sum('amount'))['t']                       # incluye los del escenario
        self.assertGreater(total, 1370000)
        self.assertIn('$' + formats.number_format(total, decimal_pos=0, force_grouping=True), html)   # total recaudado
        self.assertIn('.', formats.number_format(total, decimal_pos=0, force_grouping=True))
        self.assertIn('$120.000', html)
        self.assertIn('$1.250.000', html)
        self.assertNotRegex(html, r'\$\s*(1370000|120000|1250000)\b')


@NO_MANIFEST
class PaymentMessageTests(ScenarioTestCase):
    def test_flash_message_and_model_text_use_the_thousands_point(self):
        self.client.force_login(self.boss)
        response = self.client.post(reverse('trainer_manual_payment_add', args=[self.client_a.pk]), {
            'amount': '1250000', 'payment_date': date.today().isoformat(), 'method': 'cash'}, follow=True)
        text = ' '.join(str(m) for m in response.context['messages'])
        self.assertIn('Pago de $1.250.000 registrado', text)
        self.assertNotIn('1,250,000', text)
        self.assertIn('$1.250.000', str(ManualPayment.objects.get(user=self.client_a, amount=1250000)))


class MoneyTemplateGuardTests(SimpleTestCase):
    """Todo `$` seguido de un monto en una plantilla debe agrupar miles (`floatformat:"0g"`)."""
    MONEY = re.compile(r'\$\{\{\s*([^}|]*?(?:price|amount|amount_cop|total)[^}|]*)\|floatformat:0\s*\}\}')

    def test_no_money_is_printed_without_thousands_separator(self):
        offenders = []
        for path in sorted(Path(settings.BASE_DIR, 'templates').rglob('*.html')):
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
                if self.MONEY.search(line):
                    offenders.append(f'{path.relative_to(settings.BASE_DIR)}:{number}')
        self.assertEqual(offenders, [], 'Usa floatformat:"0g" para pesos: ' + ', '.join(offenders))
