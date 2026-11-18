"""D-02: progreso de la clienta (`trainer_client_progress`; docs/contracts/plan-ia-fase-d.md, sección 6).

El HTML es del frontend: estas pruebas miran el CONTEXTO y los encabezados. Los cálculos puros están en
`test_progress_rules.py`; aquí se prueba la lectura de la base de datos, la autorización y el
consentimiento.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.assessments.models import BodyMeasurement
from apps.health import services as health
from apps.health.models import PURPOSE_HEALTH_DATA, HealthAccessLog
from apps.health.tests.helpers import stub_missing_templates
from apps.plans import adaptation, progress, texts
from apps.plans.models import KIND_EXERCISE, KIND_NUTRITION, MealChoice, MealOption, Plan
from apps.plans.rules import adaptation as rules_adaptation
from apps.routines.models import RoutineDayLog

from .helpers import NutritionScenarioTestCase

CONTEXT_KEYS = {'client', 'client_active', 'content_visible', 'today', 'current_plans', 'measurements',
                'measurements_count', 'measurements_truncated', 'charts', 'baseline', 'last_measurement',
                'measure_due', 'measure_due_text', 'adherence'}
ADHERENCE_KEYS = {'start', 'end', 'days', 'weeks', 'training', 'meals'}
BLOCK_KEYS = {'done', 'expected', 'per_week', 'rate', 'level', 'level_text', 'has_plan', 'note'}
WEEK_KEYS = {'index', 'first_day', 'last_day', 'training_done', 'meals_done'}
MEASUREMENT_KEYS = {'pk', 'date', 'weight', 'waist_cm', 'hip_cm', 'body_fat_pct', 'imc', 'imc_classification',
                    'source', 'source_label', 'needs_review', 'is_baseline'}
CHART_KEYS = {'key', 'label', 'unit', 'count', 'has_chart', 'view_box', 'plot', 'points', 'polyline', 'y_ticks',
              'x_ticks', 'summary'}
# Consultas de la pantalla con consentimiento (sesión, la clienta, consentimiento, planes, medidas, ficha,
# rutinas y comidas); fijo aunque haya muchas medidas, marcas o planes.
EXPECTED_QUERIES = 11


def days_ago(count):
    return timezone.localdate() - timedelta(days=count)


def log_days(client, day, *offsets):
    """Un día de rutina marcado en cada fecha (`completed_at` es automático: se corrige después)."""
    for offset in offsets:
        log = RoutineDayLog.objects.create(user=client, routine_day=day)
        RoutineDayLog.objects.filter(pk=log.pk).update(completed_at=days_ago(offset))


def add_measurement(client, offset, weight, **extra):
    m = BodyMeasurement.objects.create(user=client, trainer=extra.pop('trainer', None), weight=weight, **extra)
    BodyMeasurement.objects.filter(pk=m.pk).update(date=days_ago(offset))
    m.refresh_from_db()
    return m


@stub_missing_templates()
class ProgressTestCase(NutritionScenarioTestCase):
    def page(self, client=None, user=None):
        self.login(user or self.trainer_a)
        return self.client.get(reverse('trainer_client_progress', args=[(client or self.client_a).pk]))

    def context(self, client=None, user=None):
        response = self.page(client, user)
        self.assertEqual(response.status_code, 200)
        return response.context

    def history(self, plan, days):
        """El plan se aprobó hace `days` días (para tener historial de constancia)."""
        Plan.objects.filter(pk=plan.pk).update(approved_at=timezone.now() - timedelta(days=days))

    def adherence(self, **kwargs):
        return self.context(**kwargs)['adherence']


class ProgressAuthorizationTests(ProgressTestCase):
    def test_matrix(self):
        url = reverse('trainer_client_progress', args=[self.client_a.pk])
        self.client.logout()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])
        self.assertEqual(self.page(user=self.client_a).status_code, 403)
        self.assertEqual(self.page(user=self.trainer_b).status_code, 404)
        for user in (self.trainer_a, self.boss):
            self.assertEqual(self.page(user=user).status_code, 200)
        self.assertEqual(self.page(self.client_free, self.trainer_a).status_code, 404)
        self.assertEqual(self.page(self.client_free, self.boss).status_code, 200)

    def test_only_get_no_store_and_feature_switches(self):
        self.login(self.trainer_a)
        url = reverse('trainer_client_progress', args=[self.client_a.pk])
        self.assertEqual(self.client.post(url).status_code, 405)
        self.assertIn('no-store', self.client.get(url)['Cache-Control'])
        for label, switch in (('salud apagada', {'HEALTH_FEATURES_ENABLED': False}),
                              ('IA apagada', {'AI_PLANS_MODE': 'off'})):
            with self.subTest(label), override_settings(**switch):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_inactive_is_visible_and_erased_is_a_404(self):
        User.objects.filter(pk=self.client_a.pk).update(is_active=False)
        self.assertFalse(self.context()['client_active'])
        User.objects.filter(pk=self.client_a.pk).update(erased_at=timezone.now())
        self.assertEqual(self.page().status_code, 404)

    def test_nobody_elses_measurements_or_marks_show_up_and_no_access_log_is_written(self):
        self.ready(self.client_a)
        self.ready(self.client_b)
        add_measurement(self.client_b, 1, 99)
        log_days(self.client_b, self.day_b, 1, 2, 3)
        before = HealthAccessLog.objects.count()
        context = self.context()
        self.assertEqual([m['weight'] for m in context['measurements']], [Decimal('60.0')])
        self.assertEqual(context['adherence']['training']['done'], 0)
        self.assertEqual(HealthAccessLog.objects.count(), before)          # no se abre la ficha: no hay registro


class ProgressContextTests(ProgressTestCase):
    def setUp(self):
        super().setUp()
        self.ready(self.client_a)

    def test_exact_shape(self):
        add_measurement(self.client_a, 20, 62, waist_cm=80, hip_cm=100)
        context = self.context()
        self.assertTrue(CONTEXT_KEYS <= set(context.keys()))  # ContextList: la plantilla real usa extends e include
        built = progress.build_context(self.client_a, content_visible=True)
        self.assertEqual(set(built), CONTEXT_KEYS - {'client', 'client_active', 'content_visible'})
        self.assertEqual(set(built['measurements'][0]), MEASUREMENT_KEYS)
        self.assertEqual(['weight', 'waist_cm', 'hip_cm', 'imc'], [c['key'] for c in built['charts']])
        self.assertEqual(['Peso', 'Cintura', 'Cadera', 'IMC'], [c['label'] for c in built['charts']])
        for chart in built['charts']:
            self.assertEqual(set(chart), CHART_KEYS)
        adherence = built['adherence']
        self.assertEqual(set(adherence), ADHERENCE_KEYS)
        self.assertEqual(len(adherence['weeks']), 4)
        self.assertEqual(set(adherence['weeks'][0]), WEEK_KEYS)
        self.assertEqual(set(adherence['training']), BLOCK_KEYS)
        self.assertEqual(set(adherence['meals']), BLOCK_KEYS)

    def test_measurement_table_newest_first_with_flags_and_labels(self):
        BodyMeasurement.objects.filter(user=self.client_a).update(date=days_ago(40))   # la del escenario, la más vieja
        old = add_measurement(self.client_a, 30, 64, waist_cm=82, hip_cm=101, trainer=self.trainer_a)
        flagged = add_measurement(self.client_a, 2, 90, needs_review=True, source='member')
        rows = self.context()['measurements']
        self.assertEqual([r['pk'] for r in rows][:2], [flagged.pk, old.pk])
        self.assertEqual((rows[0]['source_label'], rows[0]['needs_review']), ('Clienta', True))
        row = next(r for r in rows if r['pk'] == old.pk)
        self.assertEqual((row['source_label'], row['waist_cm'], row['hip_cm']), ('Entrenador/a', Decimal('82.0'),
                                                                                 Decimal('101.0')))
        self.assertEqual(self.context()['measurements_count'], 3)         # la del escenario + las dos nuevas

    def test_the_table_is_capped_at_sixty_and_says_so(self):
        for offset in range(1, 66):
            add_measurement(self.client_a, offset, 60 + offset / 10)
        context = self.context()
        self.assertEqual((len(context['measurements']), context['measurements_count'],
                          context['measurements_truncated']), (60, 66, True))
        self.assertEqual([c['count'] for c in context['charts']][0], 60)
        dates = [m['date'] for m in context['measurements']]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_baseline_comes_from_the_approved_plan_and_shows_the_change(self):
        approved = self.make_exercise(self.client_a, approve=True)         # medida base = la del escenario
        base = approved.measurement
        newer = add_measurement(self.client_a, 0, 57.5, waist_cm=75)
        baseline = self.context()['baseline']
        self.assertEqual((baseline['plan_kind'], baseline['plan_kind_label'], baseline['plan_version']),
                         ('exercise', 'Plan de ejercicio', approved.version))
        self.assertEqual((baseline['date'], baseline['weight']), (base.date, base.weight))
        self.assertEqual(baseline['delta']['weight'], newer.weight - base.weight)
        self.assertIsNone(baseline['delta']['waist_cm'])                   # la base no tenía cintura
        self.assertFalse(baseline['same_measurement'])
        marked = [m for m in self.context()['measurements'] if m['is_baseline']]
        self.assertEqual([m['pk'] for m in marked], [base.pk])
        chart = self.context()['charts'][0]
        self.assertEqual([p['is_baseline'] for p in chart['points']].count(True), 1)

    def test_baseline_is_none_without_a_plan_or_when_its_measurement_is_gone(self):
        self.assertIsNone(self.context()['baseline'])
        approved = self.make_exercise(self.client_a, approve=True)
        self.assertIsNotNone(self.context()['baseline'])
        BodyMeasurement.objects.filter(pk=approved.measurement_id).update(needs_review=False)
        add_measurement(self.client_a, 0, 59)
        Plan.objects.filter(pk=approved.pk).update(measurement=None)
        self.assertIsNone(self.context()['baseline'])

    def test_the_oldest_reference_wins_between_the_two_kinds(self):
        exercise = self.make_exercise(self.client_a, approve=True)
        add_measurement(self.client_a, 0, 58)
        nutrition = self.make_nutrition(self.client_a, approve=True)
        self.assertGreater(nutrition.measurement.pk, exercise.measurement.pk)   # la de alimentación es más nueva
        baseline = self.context()['baseline']
        self.assertEqual((baseline['plan_kind'], baseline['plan_version']), ('exercise', exercise.version))
        BodyMeasurement.objects.filter(pk=nutrition.measurement_id).update(date=days_ago(90))
        self.assertEqual(self.context()['baseline']['plan_kind'], 'nutrition')   # ahora la más antigua es la suya

    def test_measure_due_follows_the_14_day_rule(self):
        BodyMeasurement.objects.filter(user=self.client_a).update(date=days_ago(14))
        context = self.context()
        self.assertEqual((context['measure_due'], context['measure_due_text']), (False, ''))
        self.assertEqual(context['last_measurement'], {'date': days_ago(14), 'days_old': 14})
        BodyMeasurement.objects.filter(user=self.client_a).update(date=days_ago(15))
        context = self.context()
        self.assertEqual((context['measure_due'], context['measure_due_text']),
                         (True, 'Hace 15 días que no registra medidas.'))
        BodyMeasurement.objects.filter(user=self.client_a).delete()
        context = self.context()
        self.assertEqual((context['measure_due'], context['measure_due_text'], context['last_measurement']),
                         (True, 'Todavía no tiene medidas registradas.', None))

    def test_current_plans_header(self):
        self.assertEqual(self.context()['current_plans'], {'exercise': None, 'nutrition': None})
        approved = self.make_exercise(self.client_a, approve=True)
        info = self.context()['current_plans']['exercise']
        self.assertEqual((info['version'], info['expired']), (approved.version, False))
        Plan.objects.filter(pk=approved.pk).update(valid_until=days_ago(1))
        self.assertTrue(self.context()['current_plans']['exercise']['expired'])


class WithoutConsentTests(ProgressTestCase):
    def test_only_the_training_days_remain(self):
        self.ready(self.client_a)
        approved = self.make_exercise(self.client_a, approve=True)
        nutrition = self.make_nutrition(self.client_a, approve=True)
        self.history(approved, 40)
        self.history(nutrition, 40)
        add_measurement(self.client_a, 3, 61)
        log_days(self.client_a, self.day_a, 1, 2, 9)
        health.revoke_consent(self.client_a, PURPOSE_HEALTH_DATA)
        context = self.context()
        self.assertFalse(context['content_visible'])
        self.assertEqual((context['measurements'], context['charts'], context['baseline']), ([], [], None))
        self.assertEqual((context['measurements_count'], context['measurements_truncated']), (0, False))
        self.assertEqual((context['last_measurement'], context['measure_due'], context['measure_due_text']),
                         (None, False, ''))
        adherence = context['adherence']
        self.assertIsNone(adherence['meals'])
        training = adherence['training']
        self.assertEqual((training['done'], training['expected'], training['rate'], training['level'],
                          training['note']), (3, None, None, None, texts.NOTE_NO_PROFILE))
        self.assertEqual([w['meals_done'] for w in adherence['weeks']], [None] * 4)
        self.assertEqual(sum(w['training_done'] for w in adherence['weeks']), 3)
        self.assertTrue(context['current_plans']['exercise'])             # los datos del plan sí

    def test_no_measurement_or_meal_query_is_even_made(self):
        self.ready(self.client_a)
        health.revoke_consent(self.client_a, PURPOSE_HEALTH_DATA)
        self.login(self.trainer_a)
        with CaptureQueriesContext(connection) as queries:
            self.client.get(reverse('trainer_client_progress', args=[self.client_a.pk]))
        sql = ' '.join(q['sql'].lower() for q in queries)
        self.assertNotIn('assessments_bodymeasurement', sql)
        self.assertNotIn('plans_mealchoice', sql)
        self.assertNotIn('health_healthprofile', sql)                      # tampoco se lee la ficha


class AdherenceTests(ProgressTestCase):
    def setUp(self):
        super().setUp()
        self.ready(self.client_a)                                          # ficha con 3 días por semana
        self.exercise = self.make_exercise(self.client_a, approve=True)

    def test_training_counts_expected_and_level(self):
        self.history(self.exercise, 40)
        log_days(self.client_a, self.day_a, *range(1, 11))                 # 10 marcas en los últimos 28 días
        log_days(self.client_a, self.day_a, 29, 35)                        # fuera de la ventana
        training = self.adherence()['training']
        self.assertEqual((training['done'], training['expected'], training['per_week']), (10, 12, 3))
        self.assertEqual((training['rate'], training['level'], training['has_plan'], training['note']),
                         (83, 'high', True, ''))
        self.assertEqual(training['level_text'], texts.TRAINING_LEVEL_TEXT['high'])
        adherence = self.adherence()
        self.assertEqual((adherence['days'], adherence['end']), (28, timezone.localdate()))
        self.assertEqual(sum(w['training_done'] for w in adherence['weeks']), 10)

    def test_the_level_matches_the_adaptation_panel(self):
        self.history(self.exercise, 40)
        for count in (0, 3, 6, 7, 9, 10, 12, 15):
            RoutineDayLog.objects.all().delete()
            log_days(self.client_a, self.day_a, *[offset % 27 + 1 for offset in range(count)])
            with self.subTest(marks=count):
                profile = health.current_profile(self.client_a)
                expected = rules_adaptation.build_profile(adaptation.collect(self.client_a, profile)).adherence
                self.assertEqual(self.adherence()['training']['level'], expected)

    def test_cold_start_keeps_the_counts_but_gives_no_percentage(self):
        self.history(self.exercise, 13)
        log_days(self.client_a, self.day_a, 1, 2)
        training = self.adherence()['training']
        self.assertEqual((training['done'], training['rate'], training['level'], training['note']),
                         (2, None, None, texts.NOTE_COLD_START))
        self.history(self.exercise, 14)
        self.assertIsNotNone(self.adherence()['training']['rate'])

    def test_no_plan_ever_still_counts_a_hand_made_routine(self):
        Plan.objects.all().delete()
        log_days(self.client_a, self.day_a, 1, 5)
        training = self.adherence()['training']
        self.assertEqual((training['done'], training['has_plan'], training['rate'], training['note']),
                         (2, False, None, texts.NOTE_NO_EXERCISE_PLAN))

    def test_the_first_plan_date_is_the_one_adaptation_uses(self):
        self.history(self.exercise, 33)
        first = progress._first_approvals(self.client_a)
        self.assertEqual(first['anything'], adaptation._history_since(self.client_a))
        self.assertEqual(first['exercise'], first['anything'])
        self.assertIsNone(first['nutrition'])

    def test_meals_count_only_active_options_and_only_this_client(self):
        nutrition = self.make_nutrition(self.client_a, approve=True)
        self.history(nutrition, 40)
        slot = nutrition.nutrition.slots.get(code='lunch')
        options = list(slot.options.filter(is_active=True).order_by('position'))
        now = timezone.now()
        for offset in (1, 2, 3):                                           # 3 comidas marcadas (opción activa)
            MealChoice.objects.create(client=self.client_a, slot=slot, option=options[0], date=days_ago(offset),
                                      completed_at=now)
        MealChoice.objects.create(client=self.client_a, slot=slot, option=options[1], date=days_ago(4))   # sin marcar
        inactive = MealOption.objects.create(slot=slot, position=1, name='Rechazada', kcal=1, protein_g=1,
                                             carbs_g=1, fat_g=1, is_active=False)
        MealChoice.objects.create(client=self.client_a, slot=slot, option=inactive, date=days_ago(5),
                                  completed_at=now)                        # opción reemplazada: no cuenta
        MealChoice.objects.create(client=self.client_a, slot=slot, option=options[0], date=days_ago(40),
                                  completed_at=now)                        # fuera de la ventana
        meals = self.adherence()['meals']
        per_week = nutrition.nutrition.meals_per_day * 7
        self.assertEqual((meals['done'], meals['per_week'], meals['expected']),
                         (3, per_week, per_week * 4))
        self.assertEqual((meals['has_plan'], meals['level'], meals['note']), (True, 'low', ''))
        self.assertEqual(meals['level_text'], texts.MEALS_LEVEL_TEXT['low'])
        # si el entrenador recupera la opción anterior, sus marcas vuelven a contar
        MealOption.objects.filter(pk=options[0].pk).update(is_active=False)
        MealOption.objects.filter(pk=inactive.pk).update(is_active=True)
        self.assertEqual(self.adherence()['meals']['done'], 1)             # solo la del día 5 (opción recuperada)

    def test_meal_notes_without_plan_and_with_a_retired_one(self):
        self.assertEqual(self.adherence()['meals']['note'], texts.NOTE_NO_NUTRITION_PLAN)
        nutrition = self.make_nutrition(self.client_a, approve=True)
        self.history(nutrition, 40)
        Plan.objects.filter(pk=nutrition.pk).update(status=Plan.STATUS_ARCHIVED)
        Plan.objects.filter(pk=nutrition.pk).update(status=Plan.STATUS_SUPERSEDED)    # vigente: ninguno
        meals = self.adherence()['meals']
        self.assertEqual((meals['has_plan'], meals['rate'], meals['note']),
                         (True, None, texts.NOTE_NO_CURRENT_NUTRITION_PLAN))


class QueryCountTests(ProgressTestCase):
    def test_the_number_of_queries_does_not_grow_with_the_data(self):
        self.ready(self.client_a)
        approved = self.make_exercise(self.client_a, approve=True)
        self.history(approved, 40)
        self.login(self.trainer_a)
        url = reverse('trainer_client_progress', args=[self.client_a.pk])

        def measure():
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.client.get(url).status_code, 200)
            return len(queries)

        small = measure()
        self.assertEqual(small, EXPECTED_QUERIES)
        for offset in range(1, 40):
            add_measurement(self.client_a, offset, 60 + offset / 10, waist_cm=80)
        log_days(self.client_a, self.day_a, *range(1, 25))
        self.make_nutrition(self.client_a, approve=True)
        slot = Plan.objects.get(client=self.client_a, kind=KIND_NUTRITION, status=Plan.STATUS_APPROVED
                                ).nutrition.slots.first()
        option = slot.options.filter(is_active=True).first()
        for offset in range(1, 20):
            MealChoice.objects.create(client=self.client_a, slot=slot, option=option, date=days_ago(offset),
                                      completed_at=timezone.now())
        self.assertEqual(measure(), small + 0)
        self.assertEqual(Plan.objects.filter(client=self.client_a, kind=KIND_EXERCISE).count(), 1)
