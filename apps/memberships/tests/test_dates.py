"""Fechas de membresía en hora de Colombia (America/Bogota, UTC-5).

Se fija el reloj en 2026-03-10 02:30 UTC, que en Bogotá son las 9:30 pm del
9 de marzo. Con `timezone.now().date()` (fecha UTC) el sistema creería que ya es
el 10 de marzo y todas las fechas se correrían un día.
"""
from datetime import date, datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.memberships.models import Membership, MembershipPlan
from apps.payments.services import activate_membership

# 9:30 pm del 9 de marzo en Bogotá
NOW_UTC = datetime(2026, 3, 10, 2, 30, tzinfo=dt_timezone.utc)
HOY_LOCAL = date(2026, 3, 9)
FECHA_UTC = date(2026, 3, 10)


def fijar_reloj():
    return patch('django.utils.timezone.now', return_value=NOW_UTC)


class MembershipDatesBogotaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username='fechas', password='Clave-De-Prueba-9182!', role=User.ROLE_MEMBER,
            email='fechas@example.com',
        )
        cls.plan = MembershipPlan.objects.create(
            name='Mensual', duration_days=30, reference_price=100000,
        )

    def membership(self, end_date, **extra):
        return Membership.objects.create(
            user=self.user, plan=self.plan, start_date=HOY_LOCAL - timedelta(days=30),
            end_date=end_date, **extra,
        )

    def test_reloj_fijado_es_dia_distinto_en_utc_y_local(self):
        with fijar_reloj():
            self.assertEqual(timezone.localdate(), HOY_LOCAL)
            self.assertEqual(timezone.now().date(), FECHA_UTC)

    def test_activate_usa_fecha_local(self):
        m = Membership(user=self.user)
        with fijar_reloj():
            m.activate(self.plan, 30, activated_by=None)
        m.refresh_from_db()
        self.assertEqual(m.start_date, HOY_LOCAL)
        self.assertEqual(m.end_date, HOY_LOCAL + timedelta(days=30))

    def test_vence_hoy_sigue_valida(self):
        m = self.membership(end_date=HOY_LOCAL)
        with fijar_reloj():
            self.assertTrue(m.is_valid)
            self.assertEqual(m.days_remaining, 0)
            self.assertEqual(m.status_display, 'Activa')

    def test_vencio_ayer_esta_vencida(self):
        m = self.membership(end_date=HOY_LOCAL - timedelta(days=1))
        with fijar_reloj():
            self.assertFalse(m.is_valid)
            self.assertEqual(m.days_remaining, 0)
            self.assertEqual(m.status_display, 'Vencida')

    def test_days_remaining_cuenta_desde_fecha_local(self):
        m = self.membership(end_date=HOY_LOCAL + timedelta(days=5))
        with fijar_reloj():
            self.assertTrue(m.is_valid)
            self.assertEqual(m.days_remaining, 5)

    def test_desactivada(self):
        m = self.membership(end_date=HOY_LOCAL + timedelta(days=5), is_active=False)
        with fijar_reloj():
            self.assertFalse(m.is_valid)
            self.assertEqual(m.status_display, 'Desactivada')

    def test_renovar_vigente_extiende_desde_fin_actual(self):
        fin = HOY_LOCAL + timedelta(days=3)
        m = self.membership(end_date=fin)
        with fijar_reloj():
            m.renew(30, activated_by=None)
        m.refresh_from_db()
        self.assertEqual(m.end_date, fin + timedelta(days=30))

    def test_renovar_el_dia_de_vencimiento_extiende_desde_hoy_local(self):
        m = self.membership(end_date=HOY_LOCAL)
        with fijar_reloj():
            m.renew(30, activated_by=None)
        m.refresh_from_db()
        self.assertEqual(m.end_date, HOY_LOCAL + timedelta(days=30))

    def test_renovar_vencida_parte_desde_hoy_local(self):
        m = self.membership(end_date=HOY_LOCAL - timedelta(days=10), is_active=False)
        with fijar_reloj():
            m.renew(30, activated_by=None)
        m.refresh_from_db()
        self.assertEqual(m.end_date, HOY_LOCAL + timedelta(days=30))
        self.assertTrue(m.is_active)

    def test_pago_aprobado_activa_con_fecha_local(self):
        pago = SimpleNamespace(user=self.user, user_id=self.user.pk, plan=self.plan, reference='ref-fechas')
        with fijar_reloj():
            m = activate_membership(pago)
        m.refresh_from_db()
        self.assertEqual(m.start_date, HOY_LOCAL)
        self.assertEqual(m.end_date, HOY_LOCAL + timedelta(days=30))

    def test_pago_aprobado_renueva_membresia_que_vence_hoy(self):
        self.membership(end_date=HOY_LOCAL)
        pago = SimpleNamespace(user=self.user, user_id=self.user.pk, plan=self.plan, reference='ref-fechas')
        with fijar_reloj():
            m = activate_membership(pago)
        m.refresh_from_db()
        self.assertEqual(m.end_date, HOY_LOCAL + timedelta(days=30))
