"""Pruebas del comando `check_infra` (sin red: boto3 y SMTP simulados)."""
import json
import smtplib
import sys
from datetime import date, timedelta
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.public.management.commands import check_infra as ci

# Valores inventados que NUNCA deben aparecer en una salida
SECRETS = {
    'SECRET_KEY': 'sk-ultra-secreta-9f8e7d6c5b4a3210-abcdefghijklmnopqrstuvwxyz-0123456789',
    'R2_ACCESS_KEY_ID': 'AKIAINVENTADA1234567',
    'R2_SECRET_ACCESS_KEY': 'r2-secreto-inventado-aaaabbbbccccdddd',
    'EMAIL_HOST_PASSWORD': 'clave-app-gmail-zzzzyyyyxxxx',
    'WOMPI_PRIVATE_KEY': 'prv_test_inventada_0123456789',
    'WOMPI_INTEGRITY_SECRET': 'test_integrity_inventada_0123456789',
    'WOMPI_EVENTS_SECRET': 'test_events_inventada_0123456789',
    'DATABASE_URL': 'postgresql://usuario:clave-bd-inventada-7777@host-secreto:5432/bd',
    'R2_ENDPOINT_URL': 'https://cuenta-inventada-abc123.r2.cloudflarestorage.com',
}

R2_ENV = {
    'R2_ACCESS_KEY_ID': SECRETS['R2_ACCESS_KEY_ID'],
    'R2_SECRET_ACCESS_KEY': SECRETS['R2_SECRET_ACCESS_KEY'],
    'R2_BUCKET_NAME': 'media-bucket',
    'R2_ENDPOINT_URL': SECRETS['R2_ENDPOINT_URL'],
    'R2_PUBLIC_URL': 'https://pub-abc123.r2.dev',
    'R2_RECEIPTS_BUCKET_NAME': 'receipts-bucket',
}


class FakeClientError(Exception):
    """Imita botocore.exceptions.ClientError (tiene .response con el código de error)."""

    def __init__(self, code, message=''):
        super().__init__(message)
        self.response = {'Error': {'Code': code, 'Message': message}}


def make_settings(**overrides):
    base = dict(
        SETTINGS_MODULE='config.settings.production', DEBUG=False,
        SECRET_KEY=SECRETS['SECRET_KEY'], ALLOWED_HOSTS=['profit.up.railway.app'],
        WOMPI_PUBLIC_KEY='pub_test_x', WOMPI_PRIVATE_KEY=SECRETS['WOMPI_PRIVATE_KEY'],
        WOMPI_INTEGRITY_SECRET=SECRETS['WOMPI_INTEGRITY_SECRET'],
        WOMPI_EVENTS_SECRET=SECRETS['WOMPI_EVENTS_SECRET'],
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
        EMAIL_HOST='smtp.gmail.com', EMAIL_PORT=587, EMAIL_HOST_USER='correo@gmail.com',
        EMAIL_HOST_PASSWORD=SECRETS['EMAIL_HOST_PASSWORD'],
        HEALTH_FEATURES_ENABLED=False, PRIVACY_POLICY_URL='',
        DATABASES={'default': {}},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def make_ctx(settings_=None, env=None, **kwargs):
    return ci.Context(settings=settings_ or make_settings(), env=env if env is not None else {}, **kwargs)


def fake_s3(head_bucket=None):
    client = mock.MagicMock(spec=['head_bucket'])
    if head_bucket is not None:
        client.head_bucket.side_effect = head_bucket
    return client


def by_name(results, name):
    return next(c for c in results if c.name == name)


def all_text(results):
    return json.dumps([vars(c) for c in results], ensure_ascii=False)


class SettingsAndSecretChecks(TestCase):
    def test_produccion_sin_secret_key_es_error(self):
        ctx = make_ctx(make_settings(SECRET_KEY=''))
        result = by_name(ci.check_secret_key_and_hosts(ctx), 'SECRET_KEY')
        self.assertEqual(result.status, ci.ERROR)
        self.assertIn('SECRET_KEY', result.action)

    def test_produccion_con_secret_key_de_ejemplo_es_error(self):
        for value in ('django-insecure-dev-key-change-in-production', 'cambia-esta-clave-secreta-en-produccion'):
            ctx = make_ctx(make_settings(SECRET_KEY=value))
            self.assertEqual(by_name(ci.check_secret_key_and_hosts(ctx), 'SECRET_KEY').status, ci.ERROR)

    def test_secret_key_corta_es_aviso_y_larga_es_ok(self):
        self.assertEqual(
            by_name(ci.check_secret_key_and_hosts(make_ctx(make_settings(SECRET_KEY='corta-pero-no-obvia'))),
                    'SECRET_KEY').status, ci.WARN)
        self.assertEqual(by_name(ci.check_secret_key_and_hosts(make_ctx()), 'SECRET_KEY').status, ci.OK)

    def test_allowed_hosts_vacio_o_con_comodin_es_error(self):
        for hosts in ([], ['*'], ['profit.up.railway.app', '*']):
            ctx = make_ctx(make_settings(ALLOWED_HOSTS=hosts))
            self.assertEqual(by_name(ci.check_secret_key_and_hosts(ctx), 'ALLOWED_HOSTS').status, ci.ERROR)

    def test_fuera_de_produccion_no_se_exigen(self):
        ctx = make_ctx(make_settings(SETTINGS_MODULE='config.settings.development', DEBUG=True,
                                     SECRET_KEY='', ALLOWED_HOSTS=[]))
        self.assertTrue(all(c.status == ci.OK for c in ci.check_secret_key_and_hosts(ctx)))

    def test_debug_encendido_en_produccion_es_error(self):
        self.assertEqual(ci.check_settings(make_ctx(make_settings(DEBUG=True)))[0].status, ci.ERROR)
        self.assertEqual(ci.check_settings(make_ctx())[0].status, ci.OK)


class DatabaseChecks(TestCase):
    def test_base_de_pruebas_conecta_y_no_tiene_migraciones_pendientes(self):
        ctx = make_ctx(make_settings(SETTINGS_MODULE='config.settings.development', DEBUG=True))
        results = ci.check_database(ctx)
        self.assertEqual(by_name(results, 'Motor').status, ci.OK)
        self.assertEqual(by_name(results, 'Migraciones').status, ci.OK)

    def test_detecta_migraciones_pendientes_sin_aplicarlas(self):
        MigrationRecorder(connection).migration_qs.filter(app='public', name='0001_initial').delete()
        ctx = make_ctx(make_settings(SETTINGS_MODULE='config.settings.development', DEBUG=True))
        result = by_name(ci.check_database(ctx), 'Migraciones')
        self.assertEqual(result.status, ci.ERROR)
        self.assertIn('pendiente', result.detail)
        self.assertIn('migrate', result.action)
        # Solo lectura: la migración sigue sin registrarse como aplicada
        self.assertFalse(MigrationRecorder(connection).migration_qs
                         .filter(app='public', name='0001_initial').exists())

    def test_sqlite_en_produccion_es_error(self):
        result = by_name(ci.check_database(make_ctx()), 'Motor')   # la base de pruebas es SQLite
        self.assertEqual(result.status, ci.ERROR)
        self.assertIn('PostgreSQL', result.action)

    def test_sin_conexion_es_error_y_no_muestra_el_mensaje(self):
        from django.db import OperationalError
        boom = OperationalError('could not connect: ' + SECRETS['DATABASE_URL'])
        with mock.patch.object(ci.connection, 'ensure_connection', side_effect=boom):
            ctx = make_ctx()
            results = ci.check_database(ctx)
        self.assertEqual(results[0].status, ci.ERROR)
        self.assertFalse(ctx.db_ok)
        self.assertNotIn('clave-bd-inventada', all_text(results))
        # Sin conexión, la comprobación de la superusuaria se omite con aviso (no revienta)
        self.assertEqual(ci.check_superuser(ctx)[0].status, ci.WARN)


class PostgresVersionChecks(TestCase):
    """Versión de PostgreSQL contra la tabla de fin de soporte. Sin Postgres real: la versión del servidor
    (`server_version_num`), el motor y el día de hoy se simulan."""

    def check(self, number, today=date(2026, 11, 20), vendor='postgresql', **ctx_kw):
        ctx = make_ctx(make_settings(DATABASES={'default': {'HOST': 'host-secreto.internal',
                                                            'PASSWORD': 'clave-bd-inventada'}}),
                       pg_version=lambda: number, today=lambda: today, **ctx_kw)
        with mock.patch.object(ci, 'database_vendor', return_value=vendor):
            results = ci.check_postgres_version(ctx)
        self.assertNotIn('host-secreto', all_text(results))
        self.assertNotIn('clave-bd-inventada', all_text(results))
        return results

    def one(self, number, **kwargs):
        results = self.check(number, **kwargs)
        self.assertEqual([c.name for c in results], ['Versión de PostgreSQL'])
        self.assertEqual(results[0].area, 'Base de datos')
        return results[0]

    def test_14_is_a_warning_today_because_its_support_ended_on_2026_11_12(self):
        result = self.one(140025)
        self.assertEqual(result.status, ci.WARN)
        self.assertIn('14.25', result.detail)                       # mayor.menor
        self.assertIn('terminó su soporte el 2026-11-12', result.detail)
        self.assertIn('correcciones de seguridad', result.detail)
        self.assertIn('Django 5.2 exige PostgreSQL 14', result.action)      # la web sigue funcionando
        self.assertIn('https://www.postgresql.org/support/versioning/', result.action)
        self.assertIn('2026-11-20', result.action)                  # fecha de consulta de la tabla
        self.assertIn('docs/BACKUPS.md', result.action)

    def test_supported_versions_are_ok_and_say_until_when(self):
        for number, until in ((150020, '2027-11-11'), (160016, '2028-11-09'), (170012, '2029-11-08'),
                              (180007, '2030-11-14'), (190000, '2031-11-19')):
            with self.subTest(number=number):
                result = self.one(number)
                self.assertEqual(result.status, ci.OK)
                self.assertIn(f'soporte hasta el {until}', result.detail)
                self.assertNotIn('pronto', result.action)

    def test_the_check_uses_todays_date_so_a_version_expires_by_itself(self):
        # 14: el día antes sigue OK (con la nota de que termina pronto); el día de la última versión y después, AVISO
        self.assertEqual(self.one(140025, today=date(2026, 11, 11)).status, ci.OK)
        self.assertIn('termina pronto', self.one(140025, today=date(2026, 11, 11)).action)
        for today in (date(2026, 11, 12), date(2027, 1, 1)):
            self.assertEqual(self.one(140025, today=today).status, ci.WARN, today)
        # 15: lejos de su fin es OK sin nota; a menos de 180 días, OK con nota; después, AVISO
        self.assertNotIn('pronto', self.one(150020, today=date(2026, 11, 20)).action)
        soon = self.one(150020, today=date(2027, 6, 1))
        self.assertEqual(soon.status, ci.OK)
        self.assertIn('termina pronto (2027-11-11)', soon.action)
        self.assertEqual(self.one(150020, today=date(2027, 11, 11)).status, ci.WARN)

    def test_older_versions_are_warnings_even_the_ones_missing_from_the_table(self):
        for number, text in ((130023, '13.23'), (120022, '12.22'), (110022, '11.22'), (90624, '9.6.24')):
            with self.subTest(number=number):
                result = self.one(number)
                self.assertEqual(result.status, ci.WARN)
                self.assertIn(text, result.detail)
                self.assertIn('ya no recibe correcciones de seguridad', result.detail)
        self.assertIn('terminó su soporte el 2025-11-13', self.one(130023).detail)
        self.assertIn('anterior a las de la tabla', self.one(120022).detail)

    def test_a_major_newer_than_the_table_is_ok_with_a_note_to_update_it(self):
        result = self.one(200001)
        self.assertEqual(result.status, ci.OK)
        self.assertIn('20.1', result.detail)
        self.assertIn('más nueva que la tabla', result.detail)
        self.assertIn('POSTGRES_FINAL_RELEASE', result.action)
        self.assertIn('https://www.postgresql.org/support/versioning/', result.action)

    def test_a_table_older_than_a_year_asks_to_be_reviewed_without_changing_the_status(self):
        old = self.one(160016, today=date(2027, 12, 1))
        self.assertEqual(old.status, ci.OK)
        self.assertIn('más de un año', old.action)
        self.assertNotIn('más de un año', self.one(160016, today=date(2027, 11, 20)).action)
        self.assertIn('más de un año', self.one(200001, today=date(2028, 1, 1)).action)   # también si es desconocida
        self.assertIn('más de un año', self.one(140025, today=date(2028, 1, 1)).action)

    def test_the_table_has_the_official_dates(self):
        # Copia de https://www.postgresql.org/support/versioning/ (columna «Final Release»), consultada el 2026-11-20
        self.assertEqual(ci.POSTGRES_SUPPORT_CONSULTED, date(2026, 11, 20))
        self.assertEqual(ci.POSTGRES_FINAL_RELEASE[14], date(2026, 11, 12))
        self.assertEqual(ci.POSTGRES_FINAL_RELEASE[13], date(2025, 11, 13))
        self.assertEqual(ci.POSTGRES_FINAL_RELEASE[15], date(2027, 11, 11))
        majors = sorted(ci.POSTGRES_FINAL_RELEASE)
        self.assertEqual(majors, list(range(majors[0], majors[-1] + 1)))             # sin huecos
        dates = [ci.POSTGRES_FINAL_RELEASE[major] for major in majors]
        self.assertEqual(dates, sorted(dates))                                       # fechas crecientes

    def test_with_sqlite_or_without_connection_there_is_nothing_new(self):
        self.assertEqual(self.check(140025, vendor='sqlite'), [])
        self.assertEqual(ci.check_postgres_version(make_ctx()), [])       # motor real de las pruebas: SQLite
        broken = make_ctx(pg_version=lambda: self.fail('no debe consultarse'))
        broken.db_ok = False
        with mock.patch.object(ci, 'database_vendor', return_value='postgresql'):
            self.assertEqual(ci.check_postgres_version(broken), [])

    def test_an_unreadable_version_is_a_warning_that_never_shows_the_message(self):
        from django.db import OperationalError

        def boom():
            raise OperationalError('no se pudo conectar a ' + SECRETS['DATABASE_URL'])
        ctx = make_ctx(pg_version=boom)
        with mock.patch.object(ci, 'database_vendor', return_value='postgresql'):
            results = ci.check_postgres_version(ctx)
        self.assertEqual(results[0].status, ci.WARN)
        self.assertIn('OperationalError', results[0].detail)
        self.assertNotIn('clave-bd-inventada', all_text(results))
        for garbage in (None, 'abc', 0):
            with self.subTest(garbage=garbage), mock.patch.object(ci, 'database_vendor', return_value='postgresql'):
                results = ci.check_postgres_version(make_ctx(pg_version=lambda: garbage))
            self.assertEqual(results[0].status, ci.WARN)

    def test_split_pg_version(self):
        self.assertEqual(ci.split_pg_version(160004), (16, 4, '16.4'))
        self.assertEqual(ci.split_pg_version(140025), (14, 25, '14.25'))
        self.assertEqual(ci.split_pg_version(100001), (10, 1, '10.1'))
        self.assertEqual(ci.split_pg_version('150002'), (15, 2, '15.2'))
        self.assertEqual(ci.split_pg_version(90624), (9, 24, '9.6.24'))

    def test_the_full_command_shows_the_line_in_the_database_area_without_the_host(self):
        out = StringIO()
        env = {**SECRETS}
        with mock.patch.dict('os.environ', env), mock.patch.object(ci, 'database_vendor', return_value='postgresql'), \
                mock.patch.object(ci, 'default_pg_version', return_value=140025), \
                mock.patch.object(ci, 'default_today', return_value=date(2026, 11, 20)), \
                mock.patch.object(ci, 'default_s3_client_factory', mock.Mock(return_value=fake_s3())):
            try:
                call_command('check_infra', '--json', stdout=out)
            except CommandError:
                pass
        line = next(c for c in json.loads(out.getvalue())['checks'] if c['name'] == 'Versión de PostgreSQL')
        self.assertEqual((line['area'], line['status']), ('Base de datos', 'AVISO'))
        self.assertIn('14.25', line['detail'])
        self.assertNotIn('host-secreto', out.getvalue())
        self.assertNotIn('clave-bd-inventada', out.getvalue())


class AIExecutionChecks(TestCase):
    """Dónde corren los trabajos de IA (hilos en la web o trabajador aparte) y si hay trabajos atascados."""

    def setUp(self):
        self.member = User.objects.create_user(username='clienta-ci', password='Clave-De-Prueba-9182!', role='member')

    def queued_job(self, minutes_ago, status='queued', kind='exercise'):
        from apps.plans.models import PlanGenerationJob
        from uuid import uuid4
        return PlanGenerationJob.objects.create(
            kind=kind, client=self.member, requested_by=self.member, requested_by_role='member',
            request_token=uuid4(), status=status, created_at=timezone.now() - timedelta(minutes=minutes_ago))

    def run_check(self, **settings_kw):
        return ci.check_ai_execution(make_ctx(make_settings(**settings_kw)))

    def test_default_is_threads_in_the_web_process(self):
        for kw in ({}, {'AI_PLANS_WORKER': False}):
            with self.subTest(kw=kw):
                results = self.run_check(**kw)
                self.assertEqual([(c.area, c.name, c.status) for c in results], [('IA', 'Ejecución de trabajos', ci.OK)])
                self.assertIn('Hilos en el servidor web', results[0].detail)
                self.assertIn('docs/DEPLOY.md, sección 12', results[0].action)

    def test_threads_mode_ignores_the_queue_even_if_a_job_is_old(self):
        self.queued_job(minutes_ago=60)
        self.assertEqual([c.name for c in self.run_check(AI_PLANS_WORKER=False)], ['Ejecución de trabajos'])

    def test_worker_mode_is_reported_with_the_cost_and_sleep_warning(self):
        results = self.run_check(AI_PLANS_WORKER=True)
        line = by_name(results, 'Ejecución de trabajos')
        self.assertEqual(line.status, ci.OK)
        self.assertIn('Trabajador aparte', line.detail)
        self.assertIn('process_plan_jobs --loop', line.detail)
        self.assertIn('mantiene despierto a Postgres', line.action)
        self.assertEqual(by_name(results, 'Trabajos en cola').status, ci.OK)

    def test_worker_mode_warns_about_jobs_queued_for_more_than_five_minutes(self):
        self.queued_job(minutes_ago=6)
        result = by_name(self.run_check(AI_PLANS_WORKER=True), 'Trabajos en cola')
        self.assertEqual(result.status, ci.WARN)
        self.assertIn('1 trabajo(s) llevan más de 5 minutos en cola', result.detail)
        self.assertIn('servicio trabajador no está corriendo', result.detail)
        self.assertIn('process_plan_jobs --loop', result.action)
        self.assertIn('App Sleeping', result.action)

    def test_recent_or_not_queued_jobs_do_not_trigger_the_warning(self):
        self.queued_job(minutes_ago=4)                                             # reciente
        self.queued_job(minutes_ago=90, status='succeeded', kind='nutrition')      # viejo pero ya terminó
        self.queued_job(minutes_ago=90, status='running', kind='meal_alternative')
        self.assertEqual(by_name(self.run_check(AI_PLANS_WORKER=True), 'Trabajos en cola').status, ci.OK)

    def test_unreadable_queue_is_a_warning_not_a_crash(self):
        from django.db import OperationalError
        with mock.patch.object(ci, 'stuck_queued_job_count', side_effect=OperationalError('x')):
            results = self.run_check(AI_PLANS_WORKER=True)
        self.assertEqual(by_name(results, 'Trabajos en cola').status, ci.WARN)

    def test_without_database_the_queue_is_not_read(self):
        ctx = make_ctx(make_settings(AI_PLANS_WORKER=True))
        ctx.db_ok = False
        with mock.patch.object(ci, 'stuck_queued_job_count', side_effect=AssertionError('no debe consultarse')):
            self.assertEqual([c.name for c in ci.check_ai_execution(ctx)], ['Ejecución de trabajos'])

    def test_the_line_is_part_of_the_full_command(self):
        out = StringIO()
        with override_settings(AI_PLANS_WORKER=True), mock.patch.dict('os.environ', {}):
            try:
                call_command('check_infra', '--json', stdout=out)
            except CommandError:
                pass                # puede haber un ERROR ajeno a esta prueba; solo importa que las líneas salgan
        names = [(c['area'], c['name']) for c in json.loads(out.getvalue())['checks']]
        self.assertIn(('IA', 'Ejecución de trabajos'), names)
        self.assertIn(('IA', 'Trabajos en cola'), names)


class ConnectionReuseChecks(TestCase):
    """Conexiones persistentes a la base: solo se lee la configuración, nunca el host ni las claves."""

    def check(self, **database):
        ctx = make_ctx(make_settings(DATABASES={'default': {
            'HOST': 'host-secreto.internal', 'PASSWORD': 'clave-bd-inventada', **database}}))
        results = ci.check_connection_reuse(ctx)
        self.assertEqual([c.name for c in results], ['Conexiones persistentes'])
        self.assertNotIn('host-secreto', all_text(results))
        self.assertNotIn('clave-bd-inventada', all_text(results))
        return results[0]

    def test_con_reutilizacion_y_comprobacion_es_ok_dice_los_segundos_y_avisa_que_no_duerme(self):
        result = self.check(CONN_MAX_AGE=60, CONN_HEALTH_CHECKS=True)
        self.assertEqual(result.status, ci.OK)
        self.assertIn('60 s', result.detail)
        self.assertIn('CONN_HEALTH_CHECKS=True', result.detail)
        # La nota advierte que con conexiones abiertas el servicio deja de dormirse y cómo revertirlo
        self.assertIn('deja de dormirse', result.action)
        self.assertIn('DB_CONN_MAX_AGE', result.action)

    def test_con_cero_es_ok_y_explica_que_reutilizar_impide_app_sleeping(self):
        result = self.check(CONN_MAX_AGE=0, CONN_HEALTH_CHECKS=True)
        self.assertEqual(result.status, ci.OK)       # es lo normal con App Sleeping (el valor por defecto), no un fallo
        self.assertIn('CONN_MAX_AGE=0', result.detail)
        self.assertIn('impide App Sleeping', result.action)
        self.assertIn('DB_CONN_MAX_AGE=60', result.action)

    def test_sin_las_claves_se_trata_como_el_valor_por_defecto_de_django(self):
        self.assertIn('CONN_MAX_AGE=0', self.check().detail)
        self.assertEqual(ci.check_connection_reuse(make_ctx(make_settings(DATABASES=None)))[0].status, ci.OK)
        self.assertEqual(ci.check_connection_reuse(make_ctx(SimpleNamespace(DEBUG=False)))[0].status, ci.OK)

    def test_reutilizar_sin_comprobar_la_conexion_es_aviso(self):
        result = self.check(CONN_MAX_AGE=60, CONN_HEALTH_CHECKS=False)
        self.assertEqual(result.status, ci.WARN)
        self.assertIn('CONN_HEALTH_CHECKS', result.action)
        self.assertIn('deja de dormirse', result.action)

    def test_sin_limite_de_edad_se_dice(self):
        result = self.check(CONN_MAX_AGE=None, CONN_HEALTH_CHECKS=True)
        self.assertEqual(result.status, ci.OK)
        self.assertIn('sin límite', result.detail)
        self.assertIn('deja de dormirse', result.action)


class R2Checks(TestCase):
    def run_r2(self, env=None, client=None, **settings_kw):
        env = dict(R2_ENV if env is None else env)
        client = client or fake_s3()
        factory = mock.Mock(return_value=client)
        ctx = make_ctx(make_settings(**settings_kw), env=env, s3_client_factory=factory)
        return ci.check_r2(ctx), client, factory

    def test_configuracion_correcta_revisa_los_dos_buckets_solo_con_head_bucket(self):
        results, client, factory = self.run_r2()
        self.assertEqual([c.status for c in results if c.status != ci.OK], [])
        buckets = sorted(call.kwargs['Bucket'] for call in client.head_bucket.call_args_list)
        self.assertEqual(buckets, ['media-bucket', 'receipts-bucket'])
        factory.assert_called_once_with(SECRETS['R2_ENDPOINT_URL'], SECRETS['R2_ACCESS_KEY_ID'],
                                       SECRETS['R2_SECRET_ACCESS_KEY'])
        self.assertIn('acceso público', by_name(results, 'Bucket de comprobantes (conexión)').detail)

    def test_sin_credenciales_no_crea_cliente_ni_importa_boto3(self):
        factory = mock.Mock()
        ctx = make_ctx(env={}, s3_client_factory=factory)
        results = ci.check_r2(ctx)
        self.assertEqual(results[0].status, ci.WARN)          # en producción: se avisa
        factory.assert_not_called()
        # La fábrica real tampoco importa boto3 si no hay credenciales (con boto3 "ausente" no falla)
        with mock.patch.dict(sys.modules, {'boto3': None, 'botocore': None, 'botocore.config': None}):
            ctx = make_ctx(env={})
            self.assertEqual(ci.check_r2(ctx)[0].status, ci.WARN)

    def test_sin_credenciales_en_desarrollo_es_ok(self):
        ctx = make_ctx(make_settings(SETTINGS_MODULE='config.settings.development', DEBUG=True), env={})
        self.assertEqual(ci.check_r2(ctx)[0].status, ci.OK)

    def test_configuracion_incompleta_lista_lo_que_falta(self):
        env = dict(R2_ENV)
        env.pop('R2_ENDPOINT_URL')
        results, _client, factory = self.run_r2(env=env)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, ci.ERROR)
        self.assertIn('R2_ENDPOINT_URL', results[0].detail)
        factory.assert_not_called()

    def test_r2_public_url_mal_formada_es_error(self):
        for bad in ('http://pub-abc123.r2.dev', 'https://pub-abc123.r2.dev/media-bucket',
                    'pub-abc123.r2.dev', 'https://usuario:clave@pub-abc123.r2.dev',
                    'https://pub-abc123.r2.dev?x=1'):
            env = dict(R2_ENV, R2_PUBLIC_URL=bad)
            results, _c, _f = self.run_r2(env=env)
            self.assertEqual(by_name(results, 'R2_PUBLIC_URL').status, ci.ERROR, bad)

    def test_r2_public_url_buena_o_con_barra_final_es_ok(self):
        for good in ('https://pub-abc123.r2.dev', 'https://pub-abc123.r2.dev/', 'https://media.midominio.com:443'):
            results, _c, _f = self.run_r2(env=dict(R2_ENV, R2_PUBLIC_URL=good))
            self.assertEqual(by_name(results, 'R2_PUBLIC_URL').status, ci.OK, good)

    def test_r2_public_url_ausente_es_aviso(self):
        env = dict(R2_ENV)
        env.pop('R2_PUBLIC_URL')
        results, _c, _f = self.run_r2(env=env)
        self.assertEqual(by_name(results, 'R2_PUBLIC_URL').status, ci.WARN)

    def test_endpoint_sin_https_es_error(self):
        results, _c, _f = self.run_r2(env=dict(R2_ENV, R2_ENDPOINT_URL='http://cuenta.r2.cloudflarestorage.com'))
        self.assertEqual(by_name(results, 'R2_ENDPOINT_URL').status, ci.ERROR)

    def test_bucket_de_comprobantes_igual_al_de_media_es_error(self):
        results, client, _f = self.run_r2(env=dict(R2_ENV, R2_RECEIPTS_BUCKET_NAME='media-bucket'))
        self.assertEqual(by_name(results, 'Bucket de comprobantes').status, ci.ERROR)
        self.assertEqual(len(client.head_bucket.call_args_list), 1)   # no se repite el mismo bucket

    def test_bucket_de_comprobantes_sin_definir_es_error(self):
        env = dict(R2_ENV)
        env.pop('R2_RECEIPTS_BUCKET_NAME')
        results, _c, _f = self.run_r2(env=env)
        self.assertEqual(by_name(results, 'Bucket de comprobantes').status, ci.ERROR)

    def test_bucket_inexistente_y_sin_permiso_dan_errores_distintos(self):
        results, _c, _f = self.run_r2(client=fake_s3(FakeClientError('404', 'NoSuchBucket ' + SECRETS['R2_SECRET_ACCESS_KEY'])))
        detail = by_name(results, 'Bucket de media').detail
        self.assertEqual(by_name(results, 'Bucket de media').status, ci.ERROR)
        self.assertIn('no existe', detail)
        results, _c, _f = self.run_r2(client=fake_s3(FakeClientError('403', 'denegado')))
        self.assertIn('credenciales', by_name(results, 'Bucket de media').detail)
        self.assertNotIn('r2-secreto-inventado', all_text(results))

    def test_boto3_no_instalado_es_error_claro(self):
        factory = mock.Mock(side_effect=ImportError('No module named boto3'))
        ctx = make_ctx(env=dict(R2_ENV), s3_client_factory=factory)
        result = by_name(ci.check_r2(ctx), 'Conexión')
        self.assertEqual(result.status, ci.ERROR)
        self.assertIn('boto3', result.detail)


class WompiAndSwitchChecks(TestCase):
    def test_wompi_completo_parcial_y_ausente(self):
        self.assertEqual(ci.check_wompi(make_ctx())[0].status, ci.OK)
        partial = make_ctx(make_settings(WOMPI_EVENTS_SECRET=''))
        result = ci.check_wompi(partial)[0]
        self.assertEqual(result.status, ci.ERROR)
        self.assertIn('WOMPI_EVENTS_SECRET', result.detail)
        none = make_ctx(make_settings(WOMPI_PUBLIC_KEY='', WOMPI_PRIVATE_KEY='',
                                      WOMPI_INTEGRITY_SECRET='', WOMPI_EVENTS_SECRET=''))
        result = ci.check_wompi(none)[0]
        self.assertEqual(result.status, ci.WARN)
        self.assertIn('pagos online no funcionan', result.detail)

    def test_wompi_solo_muestra_nombres_de_variables(self):
        result = ci.check_wompi(make_ctx(make_settings(WOMPI_EVENTS_SECRET='')))[0]
        for name in ('WOMPI_PUBLIC_KEY', 'WOMPI_PRIVATE_KEY', 'WOMPI_INTEGRITY_SECRET'):
            self.assertNotIn(name, result.detail)    # las que están definidas ni se nombran
        self.assertNotIn(SECRETS['WOMPI_PRIVATE_KEY'], all_text([result]))

    def test_interruptores_de_salud(self):
        off = ci.check_health_switches(make_ctx())
        self.assertEqual(by_name(off, 'HEALTH_FEATURES_ENABLED').status, ci.OK)
        self.assertEqual(by_name(off, 'PRIVACY_POLICY_URL').status, ci.OK)
        on_no_url = ci.check_health_switches(make_ctx(make_settings(HEALTH_FEATURES_ENABLED=True)))
        self.assertEqual(by_name(on_no_url, 'PRIVACY_POLICY_URL').status, ci.WARN)
        http_url = ci.check_health_switches(make_ctx(make_settings(PRIVACY_POLICY_URL='http://x.com/p')))
        self.assertEqual(by_name(http_url, 'PRIVACY_POLICY_URL').status, ci.WARN)
        good = ci.check_health_switches(make_ctx(make_settings(PRIVACY_POLICY_URL='https://x.com/p')))
        self.assertEqual(by_name(good, 'PRIVACY_POLICY_URL').status, ci.OK)

    def test_salud_encendida_con_textos_en_borrador_es_aviso(self):
        with mock.patch.object(ci, 'purposes_pending_legal_review', return_value=['health_data']):
            results = ci.check_health_switches(make_ctx(make_settings(
                HEALTH_FEATURES_ENABLED=True, PRIVACY_POLICY_URL='https://x.com/p')))
        self.assertEqual(by_name(results, 'HEALTH_FEATURES_ENABLED').status, ci.WARN)

    def test_superusuaria(self):
        ctx = make_ctx()
        self.assertEqual(ci.check_superuser(ctx)[0].status, ci.ERROR)             # producción, ninguna
        dev = make_ctx(make_settings(SETTINGS_MODULE='config.settings.development', DEBUG=True))
        self.assertEqual(ci.check_superuser(dev)[0].status, ci.WARN)              # desarrollo, ninguna
        User.objects.create_superuser(username='yiseth', password='Clave-De-Prueba-9182!', role='trainer')
        self.assertEqual(ci.check_superuser(ctx)[0].status, ci.OK)


class EmailChecks(TestCase):
    def test_consola_en_produccion_es_aviso_y_en_desarrollo_ok(self):
        console = dict(EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend')
        self.assertEqual(ci.check_email(make_ctx(make_settings(**console)))[0].status, ci.WARN)
        dev = make_settings(SETTINGS_MODULE='config.settings.development', DEBUG=True, **console)
        self.assertEqual(ci.check_email(make_ctx(dev))[0].status, ci.OK)

    def test_smtp_sin_contrasena_es_error(self):
        ctx = make_ctx(make_settings(EMAIL_HOST_PASSWORD=''))
        self.assertEqual(ci.check_email(ctx)[0].status, ci.ERROR)

    def test_sin_flag_smtp_no_abre_ninguna_conexion(self):
        factory = mock.Mock()
        result = ci.check_email(make_ctx(mail_connection_factory=factory))[0]
        self.assertEqual(result.status, ci.OK)
        factory.assert_not_called()

    def test_con_smtp_abre_la_conexion_y_no_envia_nada(self):
        conn = mock.MagicMock()
        factory = mock.Mock(return_value=conn)
        result = ci.check_email(make_ctx(smtp=True, mail_connection_factory=factory))[0]
        self.assertEqual(result.status, ci.OK)
        conn.open.assert_called_once_with()
        conn.close.assert_called_once_with()
        conn.send_messages.assert_not_called()
        self.assertEqual(conn.method_calls, [mock.call.open(), mock.call.close()])
        self.assertEqual(mail.outbox, [])
        self.assertIn('No se envió ningún correo', result.detail)

    def test_con_smtp_credenciales_rechazadas_es_error_sin_filtrar_el_mensaje(self):
        conn = mock.MagicMock()
        conn.open.side_effect = smtplib.SMTPAuthenticationError(
            535, ('Usuario/clave rechazados: ' + SECRETS['EMAIL_HOST_PASSWORD']).encode())
        ctx = make_ctx(smtp=True, mail_connection_factory=mock.Mock(return_value=conn))
        result = ci.check_email(ctx)[0]
        self.assertEqual(result.status, ci.ERROR)
        self.assertIn('535', result.detail)
        self.assertIn('contraseña de aplicación', result.action)
        self.assertNotIn(SECRETS['EMAIL_HOST_PASSWORD'], all_text([result]))
        conn.send_messages.assert_not_called()

    def test_con_smtp_sin_red_es_error(self):
        conn = mock.MagicMock()
        conn.open.side_effect = OSError('Network is unreachable')
        result = ci.check_email(make_ctx(smtp=True, mail_connection_factory=mock.Mock(return_value=conn)))[0]
        self.assertEqual(result.status, ci.ERROR)
        self.assertIn('OSError', result.detail)


@override_settings(
    SECRET_KEY=SECRETS['SECRET_KEY'],
    WOMPI_PUBLIC_KEY='pub_test_inventada_0123456789',   # no depender del .env de quien corre las pruebas
    WOMPI_PRIVATE_KEY=SECRETS['WOMPI_PRIVATE_KEY'],
    WOMPI_INTEGRITY_SECRET=SECRETS['WOMPI_INTEGRITY_SECRET'],
    WOMPI_EVENTS_SECRET=SECRETS['WOMPI_EVENTS_SECRET'],
    EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
    EMAIL_HOST_USER='correo@gmail.com',
    EMAIL_HOST_PASSWORD=SECRETS['EMAIL_HOST_PASSWORD'],
)
class CommandTests(TestCase):
    """El comando completo: salida, JSON, código de salida y ausencia de secretos."""

    def run_command(self, *args, s3=None, mail_conn=None, env=None):
        out, err = StringIO(), StringIO()
        s3 = s3 if s3 is not None else fake_s3()
        mail_conn = mail_conn if mail_conn is not None else mock.MagicMock()
        environ = dict(env if env is not None else {**R2_ENV, **{
            k: v for k, v in SECRETS.items() if k not in R2_ENV}})
        error = None
        with mock.patch.dict('os.environ', environ), \
                mock.patch.object(ci, 'default_s3_client_factory', mock.Mock(return_value=s3)), \
                mock.patch.object(ci, 'default_mail_connection_factory', mock.Mock(return_value=mail_conn)):
            try:
                call_command('check_infra', *args, stdout=out, stderr=err)
            except CommandError as exc:
                error = exc
        return out.getvalue(), err.getvalue(), error

    def test_desarrollo_sin_errores_termina_bien_y_muestra_la_tabla(self):
        User.objects.create_superuser(username='yiseth', password='Clave-De-Prueba-9182!', role='trainer')
        out, _err, error = self.run_command()
        self.assertIsNone(error)
        self.assertIn('Resumen:', out)
        self.assertIn('0 ERROR', out)
        for area in ('Django', 'Base de datos', 'Cloudflare R2', 'Wompi', 'Correo', 'Cuentas', 'Salud'):
            self.assertIn(area, out)

    def test_el_comando_completo_incluye_las_conexiones_persistentes_sin_el_host(self):
        out, _err, _error = self.run_command('--json')
        line = next(c for c in json.loads(out)['checks'] if c['name'] == 'Conexiones persistentes')
        self.assertEqual(line['area'], 'Base de datos')
        self.assertEqual(line['status'], 'OK')
        self.assertNotIn('host-secreto', out)

    def test_con_error_el_codigo_de_salida_es_uno(self):
        # R2 con bucket de comprobantes igual al de media = ERROR
        env = {**R2_ENV, 'R2_RECEIPTS_BUCKET_NAME': R2_ENV['R2_BUCKET_NAME']}
        out, _err, error = self.run_command(env=env)
        self.assertIsNotNone(error)
        self.assertEqual(error.returncode, 1)
        self.assertIn('ERROR', out)

    def test_json_es_valido_y_resume_los_estados(self):
        User.objects.create_superuser(username='yiseth', password='Clave-De-Prueba-9182!', role='trainer')
        out, _err, error = self.run_command('--json')
        self.assertIsNone(error)
        data = json.loads(out)
        self.assertEqual(data['summary']['error'], 0)
        self.assertEqual(data['summary']['ok'] + data['summary']['aviso'], len(data['checks']))
        self.assertEqual({'area', 'name', 'status', 'detail', 'action'}, set(data['checks'][0]))

    def test_json_con_error_sigue_siendo_json_puro_y_sale_con_uno(self):
        out, _err, error = self.run_command('--json', env={**R2_ENV, 'R2_PUBLIC_URL': 'http://mal'})
        self.assertEqual(error.returncode, 1)
        self.assertGreaterEqual(json.loads(out)['summary']['error'], 1)

    def test_no_filtra_secretos_ni_en_texto_ni_en_json_ni_en_errores(self):
        boom_s3 = fake_s3(FakeClientError('403', ' '.join(SECRETS.values())))
        boom_mail = mock.MagicMock()
        boom_mail.open.side_effect = smtplib.SMTPAuthenticationError(535, ' '.join(SECRETS.values()).encode())
        for args in ((), ('--json',), ('--smtp',), ('--smtp', '--json')):
            out, err, error = self.run_command(*args, s3=boom_s3, mail_conn=boom_mail)
            blob = out + err + (str(error) if error else '')
            for name, value in SECRETS.items():
                self.assertNotIn(value, blob, f'{name} se filtró con args={args}')
            self.assertNotIn('cuenta-inventada-abc123', blob)
            self.assertNotIn('clave-bd-inventada', blob)

    def test_redaccion_de_respaldo_reemplaza_secretos_conocidos(self):
        ctx = make_ctx(env={'SECRET_KEY': SECRETS['SECRET_KEY']})
        secrets = ci.collect_secrets(ctx)
        leaked = [ci.Check('X', 'y', ci.OK, f"detalle {SECRETS['SECRET_KEY']} fin", f"accion {SECRETS['WOMPI_PRIVATE_KEY']}")]
        cleaned = ci.redact_checks(leaked, secrets)
        self.assertNotIn(SECRETS['SECRET_KEY'], all_text(cleaned))
        self.assertNotIn(SECRETS['WOMPI_PRIVATE_KEY'], all_text(cleaned))
        self.assertIn('***', cleaned[0].detail)

    def test_flag_smtp_prueba_la_conexion_sin_enviar_correos(self):
        conn = mock.MagicMock()
        self.run_command(mail_conn=conn)
        conn.open.assert_not_called()                    # sin --smtp no se toca el servidor de correo
        self.run_command('--smtp', mail_conn=conn)
        conn.open.assert_called_once_with()
        conn.send_messages.assert_not_called()
        self.assertEqual(mail.outbox, [])

    def test_fdc_key_is_redacted_from_environment_and_settings(self):
        for ctx in (make_ctx(env={'FDC_API_KEY': 'fake-fdc-env-secret'}),
                    make_ctx(make_settings(FDC_API_KEY='fake-fdc-setting-secret'))):
            checks = [ci.Check('Food', 'test', ci.OK, 'fake-fdc-env-secret fake-fdc-setting-secret')]
            cleaned = all_text(ci.redact_checks(checks, ci.collect_secrets(ctx)))
            secret = ctx.env.get('FDC_API_KEY') or ctx.settings.FDC_API_KEY
            self.assertNotIn(secret, cleaned)

    def test_produccion_sin_secret_key_es_error_en_el_comando_completo(self):
        with override_settings(SETTINGS_MODULE='config.settings.production', DEBUG=False,
                               SECRET_KEY='', ALLOWED_HOSTS=['profit.up.railway.app']):
            out, _err, error = self.run_command('--json')
        data = json.loads(out)
        secret = next(c for c in data['checks'] if c['name'] == 'SECRET_KEY')
        self.assertEqual(secret['status'], 'ERROR')
        self.assertEqual(error.returncode, 1)

    def test_migraciones_pendientes_hacen_fallar_el_comando(self):
        MigrationRecorder(connection).migration_qs.filter(app='public', name='0001_initial').delete()
        out, _err, error = self.run_command('--json')
        data = json.loads(out)
        migrations = next(c for c in data['checks'] if c['name'] == 'Migraciones')
        self.assertEqual(migrations['status'], 'ERROR')
        self.assertEqual(error.returncode, 1)

    def test_una_comprobacion_rota_no_tumba_a_las_demas(self):
        def comprobacion_rota(ctx):
            raise RuntimeError(SECRETS['SECRET_KEY'])

        with mock.patch.object(ci, 'CHECKS', (comprobacion_rota, ci.check_database)):
            ctx = make_ctx(make_settings(SETTINGS_MODULE='config.settings.development', DEBUG=True))
            results = ci.run_checks(ctx)
        broken = by_name(results, 'comprobacion_rota')
        self.assertEqual(broken.status, ci.ERROR)
        self.assertNotIn(SECRETS['SECRET_KEY'], all_text(results))
        self.assertTrue(any(c.area == 'Base de datos' for c in results))


class SecretStrengthAndStrictModeChecks(TestCase):
    """SECRET_KEY con los criterios de security.W009 y el modo SECURITY_STRICT_SECRETS."""

    def secret_check(self, **settings_kw):
        return by_name(ci.check_secret_key_and_hosts(make_ctx(make_settings(**settings_kw))), 'SECRET_KEY')

    def test_pocos_caracteres_distintos_es_aviso_sin_mostrar_la_clave(self):
        weak = 'abcd' * 15
        result = self.secret_check(SECRET_KEY=weak)
        self.assertEqual(result.status, ci.WARN)
        self.assertIn('security.W009', result.detail)
        self.assertNotIn(weak, all_text([result]))
        self.assertIn('SECURITY_STRICT_SECRETS', result.action)

    def test_clave_debil_en_modo_estricto_es_error(self):
        result = self.secret_check(SECRET_KEY='corta-pero-no-obvia', SECURITY_STRICT_SECRETS=True)
        self.assertEqual(result.status, ci.ERROR)

    def test_clave_fuerte_es_ok_en_ambos_modos(self):
        for strict in (False, True):
            self.assertEqual(self.secret_check(SECURITY_STRICT_SECRETS=strict).status, ci.OK)

    def test_se_informa_si_el_modo_estricto_esta_activo(self):
        off = by_name(ci.check_secret_key_and_hosts(make_ctx()), 'SECURITY_STRICT_SECRETS')
        self.assertEqual(off.status, ci.OK)
        self.assertIn('Inactivo', off.detail)
        on = by_name(ci.check_secret_key_and_hosts(make_ctx(make_settings(SECURITY_STRICT_SECRETS=True))),
                     'SECURITY_STRICT_SECRETS')
        self.assertIn('Activo', on.detail)


REDIS_URL_FAKE = 'redis://default:clave-redis-inventada-5555@redis-secreto.railway.internal:6379'
REDIS_CACHES = {'default': {'BACKEND': 'django_redis.cache.RedisCache', 'LOCATION': REDIS_URL_FAKE}}
LOCMEM_CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


class CacheChecks(TestCase):
    """Redis es opcional: sin él solo se avisa en producción (el despliegue no depende de Redis)."""

    def test_sin_redis_en_produccion_es_aviso_y_explica_axes_y_sesiones(self):
        ping = mock.Mock()
        result = ci.check_cache(make_ctx(make_settings(CACHES=LOCMEM_CACHES), redis_ping=ping))[0]
        self.assertEqual(result.status, ci.WARN)
        self.assertIn('REDIS_URL', result.detail)
        self.assertIn('django-axes', result.detail)
        self.assertIn('por proceso', result.detail)
        ping.assert_not_called()

    def test_sin_redis_en_desarrollo_es_ok(self):
        ctx = make_ctx(make_settings(SETTINGS_MODULE='config.settings.development', DEBUG=True,
                                     CACHES=LOCMEM_CACHES))
        self.assertEqual(ci.check_cache(ctx)[0].status, ci.OK)

    def test_sin_caches_definidas_se_trata_como_memoria_local(self):
        self.assertEqual(ci.check_cache(make_ctx())[0].status, ci.WARN)

    def test_redis_que_responde_es_ok(self):
        ping = mock.Mock(return_value=True)
        ctx = make_ctx(make_settings(CACHES=REDIS_CACHES), env={'REDIS_URL': REDIS_URL_FAKE}, redis_ping=ping)
        self.assertEqual(ci.check_cache(ctx)[0].status, ci.OK)
        ping.assert_called_once_with(REDIS_URL_FAKE)

    def test_redis_caido_es_error_sin_mostrar_la_url(self):
        ping = mock.Mock(side_effect=ConnectionError(f'no se pudo conectar a {REDIS_URL_FAKE}'))
        ctx = make_ctx(make_settings(CACHES=REDIS_CACHES), env={'REDIS_URL': REDIS_URL_FAKE}, redis_ping=ping)
        result = ci.check_cache(ctx)[0]
        self.assertEqual(result.status, ci.ERROR)
        self.assertIn('ConnectionError', result.detail)
        self.assertNotIn('clave-redis-inventada', all_text([result]))
        self.assertNotIn('redis-secreto', all_text([result]))

    def test_redis_sin_biblioteca_o_sin_pong_es_error(self):
        for ping in (mock.Mock(side_effect=ImportError), mock.Mock(return_value=False)):
            ctx = make_ctx(make_settings(CACHES=REDIS_CACHES), env={'REDIS_URL': REDIS_URL_FAKE}, redis_ping=ping)
            self.assertEqual(ci.check_cache(ctx)[0].status, ci.ERROR)

    def test_el_comando_incluye_la_cache_y_no_filtra_redis_url(self):
        out = StringIO()
        ping = mock.Mock(side_effect=OSError(REDIS_URL_FAKE))
        with override_settings(SETTINGS_MODULE='config.settings.production', DEBUG=False,
                               ALLOWED_HOSTS=['profit.up.railway.app'], CACHES=REDIS_CACHES), \
                mock.patch.dict('os.environ', {'REDIS_URL': REDIS_URL_FAKE}), \
                mock.patch.object(ci, 'default_redis_ping', ping):
            with self.assertRaises(CommandError):
                call_command('check_infra', '--json', stdout=out)
        data = json.loads(out.getvalue())
        cache = next(c for c in data['checks'] if c['area'] == 'Caché')
        self.assertEqual(cache['status'], 'ERROR')
        self.assertNotIn('clave-redis-inventada', out.getvalue())
        self.assertNotIn('redis-secreto', out.getvalue())


class NutritionRankingCheckTests(TestCase):
    def check(self, **settings_kw):
        return ci.check_nutrition_ranking(make_ctx(make_settings(**settings_kw)))[0]

    def test_rules_is_the_default_and_says_no_ai_is_called(self):
        result = self.check()
        self.assertEqual(result.status, ci.OK)
        self.assertIn('Modo rules', result.detail)
        self.assertIn('no se llama a la IA', result.detail)

    def test_ai_mode_is_reported(self):
        result = self.check(NUTRITION_RANKING_MODE='ai')
        self.assertEqual(result.status, ci.OK)
        self.assertIn('Modo ai', result.detail)

    def test_it_is_part_of_the_run(self):
        self.assertIn(ci.check_nutrition_ranking, ci.CHECKS)
