"""`count_records`: conteo por modelo para verificar una restauración (docs/BACKUPS.md).

Solo lectura y sin datos personales: lo que se imprime son nombres de modelo y números.
"""
import json
from io import StringIO

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from unittest import mock

from apps.accounts.models import User
from apps.public.management.commands import count_records


def run(*args):
    out = StringIO()
    call_command('count_records', *args, stdout=out)
    return out.getvalue()


class CountRecordsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.maria = User.objects.create_user(
            username='maria.secreta', password='Clave-De-Prueba-9182!', role='member',
            first_name='Maria Cristina', last_name='Apellido Privado', email='maria.privada@ejemplo.com')
        User.objects.create_user(username='otra.persona', password='Clave-De-Prueba-9182!', role='member')

    def test_prints_one_line_per_model_with_its_count(self):
        out = run()
        self.assertIn('Motor: sqlite', out)
        line = next(row for row in out.splitlines() if row.startswith('accounts.User '))
        self.assertEqual(line.split()[-1], str(User.objects.count()))
        self.assertEqual(User.objects.count(), 2)
        self.assertIn('Migraciones aplicadas', out)
        self.assertIn('Total de filas', out)
        # Todos los modelos de la aplicación, incluidas las tablas intermedias automáticas
        for model in apps.get_models(include_auto_created=True):
            self.assertIn(f'{model._meta.app_label}.{model._meta.object_name} ', out)

    def test_never_prints_personal_data_only_model_names_and_numbers(self):
        for args in ((), ('--json',)):
            out = run(*args)
            for secret in ('maria', 'Maria', 'Cristina', 'Apellido', 'Privado', 'privada', 'ejemplo.com',
                           'otra.persona', 'Clave-De-Prueba'):
                self.assertNotIn(secret, out, f'{secret} se filtró con args={args}')

    def test_json_is_valid_sorted_and_consistent(self):
        data = json.loads(run('--json'))
        self.assertEqual(data['engine'], 'sqlite')
        self.assertEqual(data['failed'], {})
        self.assertEqual(data['models']['accounts.User'], 2)
        self.assertEqual(data['total_rows'], sum(data['models'].values()))
        self.assertGreater(data['applied_migrations'], 0)
        self.assertEqual(list(data['models']), sorted(data['models']))
        self.assertTrue(all(isinstance(number, int) for number in data['models'].values()))

    def test_two_runs_over_the_same_data_are_identical_so_they_can_be_compared_with_diff(self):
        self.assertEqual(run(), run())
        self.assertEqual(run('--json'), run('--json'))

    def test_a_missing_row_shows_up_in_the_comparison(self):
        before = run()
        User.objects.create_user(username='tercera', password='Clave-De-Prueba-9182!', role='member')
        after = run()
        changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
        labels = {a.split()[0] for a, _b in changed}
        self.assertEqual(labels, {'accounts.User', 'Total'})
        self.assertIn('accounts.User', changed[0][0])

    def test_only_reads_with_count_queries_and_writes_nothing(self):
        with CaptureQueriesContext(connection) as queries:
            run()
        statements = [query['sql'].lstrip().upper() for query in queries.captured_queries]
        self.assertTrue(statements)
        self.assertTrue(all(sql.startswith('SELECT') for sql in statements), statements)
        self.assertTrue(all('COUNT(' in sql for sql in statements), statements)

    def test_a_model_that_cannot_be_counted_fails_with_exit_code_1_and_never_shows_the_message(self):
        class Broken:
            class _meta:
                app_label = 'prueba'
                object_name = 'Rota'

            class _base_manager:
                @staticmethod
                def count():
                    raise OperationalError('could not connect to host-secreto.internal password=clave-bd')

        real = list(apps.get_models(include_auto_created=True))
        out = StringIO()
        with mock.patch.object(count_records.apps, 'get_models', return_value=real + [Broken]):
            with self.assertRaises(CommandError) as ctx:
                call_command('count_records', stdout=out)
        self.assertEqual(ctx.exception.returncode, 1)
        self.assertIn('prueba.Rota: no se pudo contar (OperationalError)', out.getvalue())
        self.assertIn('accounts.User', out.getvalue())               # el resto sí se cuenta
        self.assertNotIn('host-secreto', out.getvalue() + str(ctx.exception))
        self.assertNotIn('clave-bd', out.getvalue() + str(ctx.exception))
