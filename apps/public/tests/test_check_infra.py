"""Pruebas del comando `check_infra` (sin red: boto3 y SMTP simulados)."""
import json
import smtplib
import sys
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.test import TestCase, override_settings

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
