"""production.py, comandos y check_infra para WhatsApp."""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from apps.accounts.tests.test_deploy_config import load_production
from apps.public.management.commands import check_infra as ci
from apps.whatsapp import conf
from apps.whatsapp.models import WhatsAppChannelState

from .helpers import WhatsAppTestCase

TOKEN = 'EAAtokenInventadoParaPruebas0123456789'
LIVE = {'WHATSAPP_CLOUD_MODE': 'live', 'WHATSAPP_CLOUD_ACCESS_TOKEN': TOKEN, 'WHATSAPP_CLOUD_PHONE_NUMBER_ID': '1',
        'WHATSAPP_CLOUD_WABA_ID': '2', 'WHATSAPP_CLOUD_APP_SECRET': 'secreto-app-inventado',
        'WHATSAPP_CLOUD_VERIFY_TOKEN': 'v' * 40, 'WHATSAPP_CLOUD_GRAPH_VERSION': 'v27.0'}


class ProductionWhatsAppSettingsTests(SimpleTestCase):
    def test_live_requires_every_variable_without_leaking_values(self):
        for name in ('WHATSAPP_CLOUD_ACCESS_TOKEN', 'WHATSAPP_CLOUD_APP_SECRET', 'WHATSAPP_CLOUD_WABA_ID'):
            with self.subTest(missing=name):
                code, _data, err = load_production(**{**LIVE, name: ''})
                self.assertNotEqual(code, 0)
                self.assertIn(name, err)
                self.assertNotIn(TOKEN, err)

    def test_live_rejects_short_token_and_bad_version_and_unknown_mode(self):
        self.assertNotEqual(load_production(**{**LIVE, 'WHATSAPP_CLOUD_VERIFY_TOKEN': 'corto'})[0], 0)
        self.assertNotEqual(load_production(**{**LIVE, 'WHATSAPP_CLOUD_GRAPH_VERSION': '27'})[0], 0)
        self.assertNotEqual(load_production(WHATSAPP_CLOUD_MODE='encendido')[0], 0)

    def test_live_complete_loads_even_with_legal_pending(self):
        code, _data, err = load_production(**LIVE)
        self.assertEqual(code, 0, err)


class ModeTests(SimpleTestCase):
    def test_defaults(self):
        with override_settings(WHATSAPP_CLOUD_MODE='', DEBUG=False):
            self.assertEqual(conf.mode(), 'off')
        with override_settings(WHATSAPP_CLOUD_MODE='', DEBUG=True):
            self.assertEqual(conf.mode(), 'simulated')
        with override_settings(WHATSAPP_CLOUD_MODE='raro'):
            self.assertEqual(conf.mode(), 'off')


class CommandTests(WhatsAppTestCase):
    def test_process_queue_off(self):
        out = StringIO()
        with override_settings(WHATSAPP_CLOUD_MODE='off'):
            call_command('process_whatsapp_queue', stdout=out)
        self.assertIn('apagados', out.getvalue())

    def test_process_queue_simulated_runs(self):
        out = StringIO()
        call_command('process_whatsapp_queue', stdout=out)
        self.assertIn('Avisos de membresía encolados: 0', out.getvalue())

    def test_process_queue_live_with_legal_pending_fails_loudly(self):
        with override_settings(**LIVE), self.assertRaises(CommandError) as ctx:
            call_command('process_whatsapp_queue', stdout=StringIO())
        self.assertIn('WHATSAPP_CLOUD_LEGAL_REVIEWED', str(ctx.exception))
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_process_queue_paused_fails(self):
        WhatsAppChannelState.objects.create(pk=1, paused=True, paused_reason='meta_190')
        with self.assertRaises(CommandError):
            call_command('process_whatsapp_queue', stdout=StringIO())

    def test_check_whatsapp_and_resume(self):
        WhatsAppChannelState.objects.create(pk=1, paused=True, paused_reason='meta_190', paused_templates=['x'])
        out = StringIO()
        call_command('check_whatsapp', '--resume', stdout=out)
        self.assertIn('Pendiente legal', out.getvalue())
        self.assertIn('Canal reanudado', out.getvalue())
        state = WhatsAppChannelState.get()
        self.assertEqual((state.paused, state.paused_templates), (False, []))

    def test_check_whatsapp_live_queries_meta_without_printing_token(self):
        fake = mock.Mock()
        fake.phone_number_info.return_value = {'quality_rating': 'GREEN', 'name_status': 'APPROVED'}
        fake.templates.return_value = [{'name': 'profit_plan_aprobado_v1', 'language': 'es_CO',
                                        'status': 'APPROVED', 'category': 'UTILITY'}]
        out = StringIO()
        with override_settings(**LIVE), mock.patch('apps.whatsapp.management.commands.check_whatsapp.get_client',
                                                   return_value=fake), self.assertRaises(CommandError):
            call_command('check_whatsapp', stdout=out)
        text = out.getvalue()
        self.assertIn('Plantilla profit_plan_aprobado_v1 aprobada', text)
        self.assertIn('Falta la plantilla profit_membresia_vencida_v1', text)
        self.assertNotIn(TOKEN, text)

    def test_register_number_only_live(self):
        with self.assertRaises(CommandError):
            call_command('whatsapp_register_number')


class CheckInfraTests(WhatsAppTestCase):
    def ctx(self, **settings_):
        from types import SimpleNamespace
        base = {'DEBUG': False, 'PRIVACY_POLICY_URL': ''}
        base.update(settings_)
        ctx = ci.Context(settings=SimpleNamespace(**base), env={})
        ctx.db_ok = True
        return ctx

    def test_off_and_live_states(self):
        self.assertEqual(ci.check_whatsapp(self.ctx(WHATSAPP_CLOUD_MODE='off'))[0].status, ci.OK)
        results = ci.check_whatsapp(self.ctx(WHATSAPP_CLOUD_MODE='live'))
        self.assertEqual(results[0].status, ci.ERROR)
        self.assertTrue(any(r.name == 'Pendientes legales' and r.status == ci.WARN for r in results))
        self.assertEqual(ci.check_whatsapp(self.ctx(WHATSAPP_CLOUD_MODE='raro'))[0].status, ci.ERROR)
