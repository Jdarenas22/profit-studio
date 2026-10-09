"""Banderas de seguridad (módulo puro, sin base de datos ni red): una prueba por código y bordes."""
import ast
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from apps.plans.rules import flags
from apps.plans.rules.flags import Facts, evaluate

TODAY = date(2026, 10, 8)
D = Decimal


def facts(**overrides):
    """Clienta sana de referencia: sin ninguna bandera ni dato faltante (IMC 22)."""
    base = dict(
        age=30, goal='toning', sleep_hours=D('7'), weight_kg=D('60'), height_m=D('1.65'),
        measurement_days_old=3, today=TODAY,
    )
    base.update(overrides)
    return Facts(**base)


def with_bmi(bmi, **overrides):
    """Facts con ese IMC exacto (estatura 2,0 m: peso = IMC x 4)."""
    return facts(weight_kg=D(str(bmi)) * 4, height_m=D('2.0'), **overrides)


def parq(*yes):
    return {f'q{i}': (f'q{i}' in yes) for i in range(1, 8)}


class FlagTestCase(TestCase):
    def codes(self, **overrides):
        return evaluate(facts(**overrides)).codes

    def report(self, **overrides):
        return evaluate(facts(**overrides))

    def assertFlag(self, code, **overrides):
        self.assertIn(code, self.codes(**overrides), overrides)

    def assertNoFlag(self, code, **overrides):
        self.assertNotIn(code, self.codes(**overrides), overrides)


class BaselineTests(FlagTestCase):
    def test_healthy_reference_has_no_flags_at_all(self):
        report = self.report()
        self.assertEqual(report.codes, ())
        self.assertEqual(report.allowed_scopes, ('exercise', 'nutrition'))
        self.assertFalse(report.has_red)

    def test_every_documented_code_exists(self):
        expected = ([f'R{i:02d}' for i in range(1, 16)] + [f'Y{i:02d}' for i in range(1, 14)]
                    + ['I01', 'I02'] + [f'M{i:02d}' for i in range(1, 10)])
        known = {code.split('_')[0] for code in flags._TEXTS}
        self.assertTrue(set(expected) <= known, set(expected) - known)

    def test_no_profile_reports_only_missing_data(self):
        report = evaluate(Facts(has_profile=False, measurement_days_old=None, today=TODAY))
        self.assertEqual(report.red, ())
        self.assertEqual(report.yellow, ())
        self.assertIn('M04_NO_PROFILE', report.codes)
        self.assertIn('M01_NO_MEASURE', report.codes)


class RedFlagTests(FlagTestCase):
    def test_r01_minor_borders(self):
        self.assertFlag('R01_MINOR', age=17)
        self.assertNoFlag('R01_MINOR', age=18)
        report = self.report(age=17)
        self.assertEqual(report.blocked_scopes, {'exercise', 'nutrition'})

    def test_r02_pregnancy(self):
        for status in ('pregnant', 'lactating', 'postpartum_under_6m'):
            self.assertFlag('R02_PREGNANCY', pregnancy_status=status)
        self.assertNoFlag('R02_PREGNANCY', pregnancy_status='none')

    def test_r03_eating_disorder_blocks_nutrition_and_warns_exercise(self):
        for value in ('yes', 'prefer_not_say'):
            report = self.report(eating_disorder_history=value)
            self.assertIn('R03_EATING_DISORDER', report.codes)
            self.assertIn('Y10_ED_EXERCISE', report.codes)
            self.assertEqual(report.blocked_scopes, {'nutrition'})
            self.assertEqual(report.yellow_scopes, {'exercise'})
            self.assertTrue(report.can_generate('exercise'))
            self.assertFalse(report.can_generate('nutrition'))
        self.assertNoFlag('R03_EATING_DISORDER', eating_disorder_history='no')

    def test_r04_symptoms_block_exercise_and_warn_nutrition(self):
        for key in ('q2', 'q3', 'q4'):
            report = self.report(parq=parq(key))
            self.assertIn('R04_PARQ_SYMPTOMS', report.codes, key)
            self.assertEqual(report.blocked_scopes, {'exercise'})
            self.assertEqual(report.yellow_scopes, {'nutrition'})
        self.assertNoFlag('R04_PARQ_SYMPTOMS', parq=parq('q1', 'q5', 'q6', 'q7'))

    def test_r05_cardiac(self):
        self.assertFlag('R05_CARDIAC', parq=parq('q1'))
        for condition in ('heart_disease', 'arrhythmia', 'stroke_history'):
            self.assertFlag('R05_CARDIAC', conditions=(condition,))
        self.assertNoFlag('R05_CARDIAC', conditions=('asthma',))

    def test_r06_needs_clearance(self):
        for key in ('q5', 'q6', 'q7'):
            self.assertFlag('R06_NEEDS_CLEARANCE', parq=parq(key))
        self.assertNoFlag('R06_NEEDS_CLEARANCE', parq=parq())
        report = self.report(parq=parq('q6'))
        self.assertEqual(report.blocked_scopes, {'exercise'})

    def test_r06_clearance_turns_it_into_y05(self):
        kwargs = dict(parq=parq('q6'), medical_clearance=True)
        report = self.report(clearance_date=date(2026, 1, 15), **kwargs)
        self.assertNotIn('R06_NEEDS_CLEARANCE', report.codes)
        self.assertIn('Y05_CLEARED', report.codes)

    def test_clearance_expires_at_exactly_12_months(self):
        kwargs = dict(parq=parq('q6'), medical_clearance=True)
        # Un día antes de cumplir los 12 meses: vigente
        self.assertNoFlag('R06_NEEDS_CLEARANCE', clearance_date=date(2025, 10, 9), **kwargs)
        # Justo a los 12 meses: vencida
        report = self.report(clearance_date=date(2025, 10, 8), **kwargs)
        self.assertIn('R06_NEEDS_CLEARANCE', report.codes)
        self.assertIn('M06_CLEARANCE_EXPIRED', report.codes)
        self.assertNotIn('Y05_CLEARED', report.codes)

    def test_future_or_missing_clearance_date_is_not_valid(self):
        kwargs = dict(parq=parq('q6'), medical_clearance=True)
        self.assertFlag('R06_NEEDS_CLEARANCE', clearance_date=date(2026, 10, 9), **kwargs)
        self.assertFlag('R06_NEEDS_CLEARANCE', clearance_date=None, **kwargs)
        # Declarar autorización sin que la ficha la respalde nunca la hace valer
        self.assertFlag('R06_NEEDS_CLEARANCE', parq=parq('q5'), medical_clearance=False,
                        clearance_date=date(2026, 9, 1))

    def test_clearance_on_feb_29(self):
        self.assertEqual(flags.add_months(date(2024, 2, 29), 12), date(2025, 2, 28))
        self.assertTrue(flags.clearance_is_valid(True, date(2024, 2, 29), date(2025, 2, 27)))
        self.assertFalse(flags.clearance_is_valid(True, date(2024, 2, 29), date(2025, 2, 28)))

    def test_r07_bmi_low_borders(self):
        self.assertFlag('R07_BMI_LOW', **{'weight_kg': D('69.96'), 'height_m': D('2.0')})      # IMC 17,49
        self.assertEqual(evaluate(with_bmi('17.49', goal='cardio')).blocked_scopes, {'exercise', 'nutrition'})
        report = evaluate(with_bmi('17.5', goal='cardio'))
        self.assertNotIn('R07_BMI_LOW', report.codes)                                   # IMC 17,5
        # 17,5 a 18,49 solo preocupa si la meta es bajar de peso o tonificar
        for goal in ('weight_loss', 'toning'):
            report = evaluate(with_bmi('18.49', goal=goal))
            self.assertIn('R07_BMI_LOW', report.codes)
            self.assertEqual(report.blocked_scopes, {'nutrition'})
        self.assertNotIn('R07_BMI_LOW', evaluate(with_bmi('18.49', goal='muscle_gain')).codes)
        self.assertNotIn('R07_BMI_LOW', evaluate(with_bmi('18.5', goal='weight_loss')).codes)

    def test_r08_bmi_very_high_and_y09_borders(self):
        report = evaluate(with_bmi('40'))
        self.assertIn('R08_BMI_VERY_HIGH', report.codes)
        self.assertEqual(report.blocked_scopes, {'nutrition'})
        self.assertEqual(report.yellow_scopes, {'exercise'})
        self.assertNotIn('Y09_BMI_HIGH', report.codes)
        report = evaluate(with_bmi('39.9'))
        self.assertNotIn('R08_BMI_VERY_HIGH', report.codes)
        self.assertIn('Y09_BMI_HIGH', report.codes)
        self.assertIn('Y09_BMI_HIGH', evaluate(with_bmi('35')).codes)
        self.assertNotIn('Y09_BMI_HIGH', evaluate(with_bmi('34.9')).codes)

    def test_r09_type1_and_insulin(self):
        self.assertFlag('R09_DIABETES_T1_INSULIN', conditions=('diabetes_t1',))
        self.assertFlag('R09_DIABETES_T1_INSULIN', medications=('insulin',))
        self.assertFlag('R09_DIABETES_T1_INSULIN', medications=('hypoglycemic_drugs',))
        self.assertNoFlag('R09_DIABETES_T1_INSULIN', conditions=('diabetes_t2',), condition_controlled=True)

    def test_r10_kidney_blocks_nutrition_only(self):
        report = self.report(conditions=('kidney_disease',))
        self.assertIn('R10_KIDNEY', report.codes)
        self.assertEqual(report.blocked_scopes, {'nutrition'})
        self.assertEqual(report.yellow_scopes, {'exercise'})

    def test_r11_liver_metabolic_bariatric(self):
        for condition in ('liver_disease', 'metabolic_disorder', 'bariatric_surgery'):
            self.assertFlag('R11_LIVER_METABOLIC', conditions=(condition,))
        self.assertNoFlag('R11_LIVER_METABOLIC', conditions=('fatty_liver',))

    def test_r12_cancer(self):
        report = self.report(conditions=('cancer_active',))
        self.assertIn('R12_CANCER', report.codes)
        self.assertEqual(report.blocked_scopes, {'exercise', 'nutrition'})

    def test_r13_recent_surgery(self):
        self.assertFlag('R13_RECENT_SURGERY', recent_surgery='under_6m')
        for value in ('none', '6_12m', 'over_12m'):
            self.assertNoFlag('R13_RECENT_SURGERY', recent_surgery=value)

    def test_r14_not_controlled(self):
        for condition in ('hypertension', 'thyroid_disease', 'asthma'):
            report = self.report(conditions=(condition,))
            self.assertIn('R14_NOT_CONTROLLED', report.codes, condition)
            self.assertEqual(report.blocked_scopes, {'exercise'}, condition)
        report = self.report(conditions=('diabetes_t2',))
        self.assertEqual(report.blocked_scopes, {'exercise', 'nutrition'})

    def test_r14_needs_both_control_and_valid_clearance(self):
        clearance = dict(medical_clearance=True, clearance_date=date(2026, 3, 1))
        self.assertFlag('R14_NOT_CONTROLLED', conditions=('hypertension',), condition_controlled=True)
        self.assertFlag('R14_NOT_CONTROLLED', conditions=('hypertension',), **clearance)
        self.assertFlag('R14_NOT_CONTROLLED', conditions=('hypertension',), condition_controlled=True,
                        medical_clearance=True, clearance_date=date(2025, 10, 8))        # vencida
        report = self.report(conditions=('hypertension',), condition_controlled=True, **clearance)
        self.assertNotIn('R14_NOT_CONTROLLED', report.codes)
        self.assertIn('Y01_HYPERTENSION', report.codes)
        self.assertFalse(report.has_red)

    def test_r15_and_y08_age_borders(self):
        self.assertNoFlag('Y08_AGE_SENIOR', age=69)
        self.assertFlag('Y08_AGE_SENIOR', age=70)
        self.assertFlag('Y08_AGE_SENIOR', age=79)
        self.assertNoFlag('R15_AGE_HIGH', age=79)
        self.assertFlag('R15_AGE_HIGH', age=80)
        self.assertNoFlag('Y08_AGE_SENIOR', age=80)
        self.assertEqual(self.report(age=80).blocked_scopes, {'exercise'})


class YellowAndInfoTests(FlagTestCase):
    CONTROLLED = dict(condition_controlled=True, medical_clearance=True, clearance_date=date(2026, 5, 1))

    def test_y02_y03_need_control_but_prediabetes_always_warns(self):
        self.assertFlag('Y02_DIABETES_T2', conditions=('diabetes_t2',), **self.CONTROLLED)
        self.assertFlag('Y02_DIABETES_T2', conditions=('prediabetes',))
        self.assertFlag('Y03_THYROID_ASTHMA', conditions=('asthma',), **self.CONTROLLED)
        self.assertFlag('Y03_THYROID_ASTHMA', conditions=('thyroid_disease',), **self.CONTROLLED)
        self.assertNoFlag('Y03_THYROID_ASTHMA', conditions=('asthma',))        # sin control es roja (R14)

    def test_y04_joint(self):
        self.assertFlag('Y04_JOINT', injuries=('knee',))
        self.assertFlag('Y04_JOINT', conditions=('osteoporosis',))
        self.assertFlag('Y04_JOINT', conditions=('herniated_disc',))
        self.assertFlag('Y04_JOINT', parq=parq('q5'), medical_clearance=True, clearance_date=date(2026, 5, 1))
        self.assertNoFlag('Y04_JOINT')

    def test_y06_anticoagulants(self):
        self.assertFlag('Y06_ANTICOAGULANTS', medications=('anticoagulants',))
        self.assertNoFlag('Y06_ANTICOAGULANTS', medications=('beta_blockers',))

    def test_y07_severe_allergy_or_celiac(self):
        self.assertFlag('Y07_SEVERE_ALLERGY', allergies=('peanut',), allergy_severity='anaphylaxis')
        self.assertNoFlag('Y07_SEVERE_ALLERGY', allergies=('peanut',), allergy_severity='mild')
        self.assertNoFlag('Y07_SEVERE_ALLERGY', allergies=(), allergy_severity='anaphylaxis')
        self.assertFlag('Y07_SEVERE_ALLERGY', conditions=('celiac',))

    def test_y11_rehab_goal(self):
        report = self.report(goal='rehab')
        self.assertIn('Y11_REHAB_GOAL', report.codes)
        without = [f for f in report.yellow if f.code == 'Y11_REHAB_GOAL'][0]
        self.assertIn('sin lesión', without.text)
        with_injury = [f for f in self.report(goal='rehab', injuries=('knee',)).yellow
                       if f.code == 'Y11_REHAB_GOAL'][0]
        self.assertNotIn('sin lesión', with_injury.text)

    def test_y12_other_text(self):
        self.assertFlag('Y12_OTHER_TEXT', conditions=('other',))
        self.assertFlag('Y12_OTHER_TEXT', medications=('other',))
        self.assertFlag('Y12_OTHER_TEXT', has_other_text=True)
        self.assertNoFlag('Y12_OTHER_TEXT')

    def test_y13_gi_and_intolerances(self):
        for condition in ('reflux_gastritis', 'ibs', 'gout'):
            self.assertFlag('Y13_GI', conditions=(condition,))
        self.assertFlag('Y13_GI', intolerances=('lactose',))
        self.assertNoFlag('Y13_GI')

    def test_i01_sleep_border(self):
        self.assertFlag('I01_SLEEP', sleep_hours=D('4.9'))
        self.assertNoFlag('I01_SLEEP', sleep_hours=D('5'))

    def test_i02_age_mismatch_border(self):
        self.assertFlag('I02_AGE_MISMATCH', age=30, assessment_age=32)
        self.assertFlag('I02_AGE_MISMATCH', age=30, assessment_age=27)
        self.assertNoFlag('I02_AGE_MISMATCH', age=30, assessment_age=31)
        self.assertNoFlag('I02_AGE_MISMATCH', age=30, assessment_age=None)

    def test_yellow_flags_do_not_block(self):
        report = self.report(injuries=('knee',), medications=('anticoagulants',))
        self.assertFalse(report.has_red)
        self.assertEqual(report.blocked_scopes, frozenset())
        self.assertEqual(report.yellow_scopes, {'exercise', 'nutrition'})
        self.assertTrue(all(f.blocks == () for f in report.yellow))


class MissingDataTests(FlagTestCase):
    def test_m01_no_measurement(self):
        self.assertFlag('M01_NO_MEASURE', measurement_days_old=None)

    def test_m02_old_measurement_border(self):
        self.assertNoFlag('M02_MEASURE_OLD', measurement_days_old=30)
        self.assertFlag('M02_MEASURE_OLD', measurement_days_old=31)

    def test_m03_no_height(self):
        self.assertFlag('M03_NO_HEIGHT', height_m=None)

    def test_m04_profile_unconfirmed(self):
        self.assertFlag('M04_PROFILE_UNCONFIRMED', profile_confirmed=False)
        self.assertNoFlag('M04_PROFILE_UNCONFIRMED')

    def test_m05_no_goal(self):
        self.assertFlag('M05_NO_GOAL', goal='')

    def test_m06_expired_clearance(self):
        self.assertFlag('M06_CLEARANCE_EXPIRED', medical_clearance=True, clearance_date=date(2025, 10, 8))
        self.assertNoFlag('M06_CLEARANCE_EXPIRED', medical_clearance=True, clearance_date=date(2025, 10, 9))
        self.assertNoFlag('M06_CLEARANCE_EXPIRED', medical_clearance=False)

    def test_m07_no_sleep(self):
        self.assertFlag('M07_NO_SLEEP', sleep_hours=None)

    def test_m08_incoherent_bmi_or_age(self):
        self.assertFlag('M08_INCOHERENT', weight_kg=D('20'), height_m=D('2.5'))        # IMC 3,2
        self.assertFlag('M08_INCOHERENT', weight_kg=D('400'), height_m=D('1.0'))       # IMC 400
        self.assertFlag('M08_INCOHERENT', age=101)
        self.assertNoFlag('M08_INCOHERENT')
        # Con un IMC incoherente no se dispara ninguna bandera basada en el IMC
        report = self.report(weight_kg=D('20'), height_m=D('2.5'))
        self.assertNotIn('R07_BMI_LOW', report.codes)

    def test_m09_no_ai_consent(self):
        self.assertFlag('M09_NO_AI_CONSENT', ai_consent=False)

    def test_missing_flags_never_block(self):
        report = self.report(measurement_days_old=None, height_m=None, goal='', ai_consent=False,
                             sleep_hours=None, profile_confirmed=False)
        self.assertFalse(report.has_red)
        self.assertEqual(report.blocked_scopes, frozenset())
        self.assertEqual(len(report.missing), 6)


class ReportShapeTests(FlagTestCase):
    def test_flags_carry_text_and_action_for_the_trainer(self):
        report = self.report(conditions=('cancer_active',))
        flag = report.red[0]
        self.assertEqual(flag.severity, 'red')
        self.assertTrue(flag.text and flag.action)
        self.assertEqual(flag.blocks, ('exercise', 'nutrition'))

    def test_yellow_scopes_exclude_blocked_ones(self):
        report = self.report(eating_disorder_history='yes', conditions=('cancer_active',))
        self.assertEqual(report.blocked_scopes, {'exercise', 'nutrition'})
        self.assertEqual(report.yellow_scopes, frozenset())
        self.assertEqual(report.allowed_scopes, ())

    def test_evaluate_is_deterministic(self):
        one = evaluate(facts(conditions=('asthma',), parq=parq('q5')))
        two = evaluate(facts(conditions=('asthma',), parq=parq('q5')))
        self.assertEqual(one, two)


class PureModuleTests(TestCase):
    ROOT = Path(__file__).resolve().parents[3]
    MODULE = ROOT / 'apps' / 'plans' / 'rules' / 'flags.py'

    def test_imports_only_the_standard_library(self):
        tree = ast.parse(self.MODULE.read_text(encoding='utf-8'))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or '').split('.')[0])
        self.assertTrue(imported <= {'calendar', 'dataclasses', 'datetime', 'decimal'}, imported)

    def test_importable_without_django_or_network_libraries(self):
        code = ('import sys; import apps.plans.rules.flags; '
                "bad = [m for m in sys.modules if m.split('.')[0] in ('django', 'requests', 'urllib3')]; "
                'sys.exit(1 if bad else 0)')
        result = subprocess.run([sys.executable, '-c', code], cwd=self.ROOT, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
