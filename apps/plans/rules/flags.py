"""Banderas de seguridad de la ficha de salud (rojas, amarillas, informativas y datos faltantes).

Módulo PURO: solo biblioteca estándar. Sin Django, sin base de datos y sin red, para poder
probarlo y reutilizarlo en el motor de reglas de la fase B. El adaptador que arma `Facts` desde la
ficha y las medidas vive en `apps/health/services.py` (`profile_flags`).

Referencia: docs/contracts/plan-ia.md, secciones 3.1 a 3.3.

[A VALIDAR] Todos los umbrales y las reglas de este archivo son una propuesta técnica y deben ser
revisados por un profesional de la salud / nutrición antes de producción. No es asesoría médica.

Alcances: `exercise` (plan de ejercicio) y `nutrition` (plan de alimentación).
- Bandera roja: bloquea la generación automática en los alcances de `scopes` (y deriva).
- Bandera amarilla: se genera con advertencias y revisión obligatoria del entrenador.
- Informativa: solo se muestra.
- Dato faltante (`M..`): lo que impide o debilita la generación; la clienta puede completarlo.
Los rojos que bloquean un solo alcance (R03, R04, R08, R10) dejan el otro alcance en amarillo.

Estas banderas son SOLO para el entrenador: nunca se muestran a la clienta.
"""
import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

RULESET_VERSION = 'flags/1'

SCOPE_EXERCISE = 'exercise'
SCOPE_NUTRITION = 'nutrition'
ALL_SCOPES = (SCOPE_EXERCISE, SCOPE_NUTRITION)

SEVERITY_RED = 'red'
SEVERITY_YELLOW = 'yellow'
SEVERITY_INFO = 'info'
SEVERITY_MISSING = 'missing'

# ─── Umbrales [A VALIDAR] ─────────────────────────────────────────────────────
ADULT_AGE = 18
SENIOR_AGE = 70                      # Y08: 70 a 79 años
VERY_OLD_AGE = 80                    # R15: 80 años o más
MAX_REASONABLE_AGE = 100
BMI_VERY_LOW = Decimal('17.5')       # R07: menor de 17,5
BMI_LOW = Decimal('18.5')            # R07: 17,5 a 18,49 con meta de bajar de peso o tonificar
BMI_HIGH = Decimal('35')             # Y09: 35 a 39,9
BMI_VERY_HIGH = Decimal('40')        # R08: 40 o más
BMI_COHERENT_MIN, BMI_COHERENT_MAX = Decimal('8'), Decimal('100')
CLEARANCE_MONTHS = 12                # autorización médica vigente: menos de 12 meses exactos
MEASURE_MAX_DAYS = 30                # medida de más de 30 días = vieja
SLEEP_LOW_HOURS = Decimal('5')       # I01: menos de 5 horas
AGE_MISMATCH_YEARS = 2               # I02: edad de la valoración vs fecha de nacimiento

GOALS_REDUCING = ('weight_loss', 'toning')   # meta de bajar de peso o tonificar
CARDIAC_CONDITIONS = ('heart_disease', 'arrhythmia', 'stroke_history')
LIVER_METABOLIC_CONDITIONS = ('liver_disease', 'metabolic_disorder', 'bariatric_surgery')
INTERMEDIATE_CONDITIONS = ('hypertension', 'diabetes_t2', 'thyroid_disease', 'asthma')
JOINT_CONDITIONS = ('osteoporosis', 'herniated_disc')
GI_CONDITIONS = ('reflux_gastritis', 'ibs', 'gout')
T1_MEDICATIONS = ('insulin', 'hypoglycemic_drugs')


@dataclass(frozen=True)
class Flag:
    code: str
    severity: str
    text: str
    action: str = ''
    scopes: tuple = ()

    @property
    def blocks(self):
        """Alcances que bloquea (solo las rojas bloquean)."""
        return self.scopes if self.severity == SEVERITY_RED else ()


@dataclass(frozen=True)
class FlagReport:
    red: tuple = ()
    yellow: tuple = ()
    info: tuple = ()
    missing: tuple = ()
    blocked_scopes: frozenset = frozenset()
    yellow_scopes: frozenset = frozenset()

    @property
    def has_red(self):
        return bool(self.red)

    @property
    def allowed_scopes(self):
        return tuple(scope for scope in ALL_SCOPES if scope not in self.blocked_scopes)

    def can_generate(self, scope):
        return scope not in self.blocked_scopes

    @property
    def all_flags(self):
        return self.red + self.yellow + self.info + self.missing

    @property
    def codes(self):
        return tuple(flag.code for flag in self.all_flags)

    def has(self, code):
        return any(flag.code == code for flag in self.all_flags)


@dataclass(frozen=True)
class Facts:
    """Lo que el motor necesita saber, ya como datos simples (sin objetos de Django)."""
    age: int | None = None                       # por fecha de nacimiento
    assessment_age: int | None = None            # edad de la última valoración inicial (I02)
    sex: str = ''
    goal: str = ''
    conditions: tuple = ()
    condition_controlled: bool = False
    medical_clearance: bool = False
    clearance_date: date | None = None
    medications: tuple = ()
    injuries: tuple = ()
    recent_surgery: str = 'none'
    pregnancy_status: str = 'none'
    eating_disorder_history: str = 'no'
    parq: dict = field(default_factory=dict)     # {'q1': True, ...}
    has_other_text: bool = False                 # texto libre en otra condición/medicamento
    allergies: tuple = ()
    allergy_severity: str = ''
    intolerances: tuple = ()
    sleep_hours: Decimal | None = None
    weight_kg: Decimal | None = None
    height_m: Decimal | None = None
    measurement_days_old: int | None = None      # None = sin medida
    has_profile: bool = True
    profile_confirmed: bool = True
    ai_consent: bool = True
    today: date | None = None                    # por defecto, hoy

    def parq_yes(self, *keys):
        return any(bool(self.parq.get(key)) for key in keys)


# ─── Fechas y cálculos ────────────────────────────────────────────────────────

def add_months(day, months):
    """`day` + `months` meses (el 29 de febrero pasa al 28 en año no bisiesto)."""
    total = day.year * 12 + (day.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


def clearance_is_valid(medical_clearance, clearance_date, today):
    """Autorización médica vigente: declarada, con fecha no futura y de menos de 12 meses exactos."""
    if not medical_clearance or clearance_date is None:
        return False
    return clearance_date <= today < add_months(clearance_date, CLEARANCE_MONTHS)


def bmi(facts):
    """IMC con Decimal, o None si falta el peso o la estatura."""
    if facts.weight_kg is None or facts.height_m is None or facts.height_m <= 0:
        return None
    return Decimal(facts.weight_kg) / (Decimal(facts.height_m) ** 2)


def bmi_is_coherent(value):
    return value is not None and BMI_COHERENT_MIN <= value <= BMI_COHERENT_MAX


# ─── Textos (para el entrenador; sin diagnósticos hacia la clienta) ───────────

_TEXTS = {
    'R01_MINOR': ('Menor de 18 años.',
                  'Derivar: plan manual con acudiente o profesional.'),
    'R02_PREGNANCY': ('Embarazo, lactancia o posparto de menos de 6 meses.',
                      'Derivar a ginecología / nutrición.'),
    'R03_EATING_DISORDER': ('Antecedentes de trastorno de la conducta alimentaria (o prefirió no decir).',
                            'Derivar a un profesional. El ejercicio queda con advertencia.'),
    'R04_PARQ_SYMPTOMS': ('Cuestionario de aptitud: dolor de pecho o mareo / desmayo.',
                          'Derivar a médico. La alimentación queda con advertencia.'),
    'R05_CARDIAC': ('Problema cardiaco declarado (cuestionario o condición).',
                    'Derivar a médico / cardiología.'),
    'R06_NEEDS_CLEARANCE': ('Cuestionario de aptitud con respuestas que exigen autorización médica y no hay una vigente.',
                            'Pedir autorización médica (vigente: menos de 12 meses).'),
    'R07_BMI_LOW': ('IMC bajo.', 'Derivar a nutrición clínica.'),
    'R08_BMI_VERY_HIGH': ('IMC de 40 o más.',
                          'Derivar a médico / nutrición. El ejercicio queda con advertencia.'),
    'R09_DIABETES_T1_INSULIN': ('Diabetes tipo 1, o uso de insulina o de medicamentos para bajar el azúcar.',
                                'Derivar (riesgo de hipoglucemia).'),
    'R10_KIDNEY': ('Enfermedad renal declarada (la ficha no permite estadificarla: se asume lo más seguro).',
                   'Derivar a nefrología / nutrición renal. El ejercicio queda con advertencia.'),
    'R11_LIVER_METABOLIC': ('Enfermedad hepática, trastorno metabólico o cirugía bariátrica.',
                            'Derivar a su especialista.'),
    'R12_CANCER': ('Cáncer en tratamiento o seguimiento.', 'Derivar a oncología.'),
    'R13_RECENT_SURGERY': ('Cirugía de hace menos de 6 meses.', 'Derivar; volver con el alta médica.'),
    'R14_NOT_CONTROLLED': ('Condición que exige control y autorización médica vigente, y falta alguna de las dos.',
                           'Pedir constancia de control y autorización médica vigente.'),
    'R15_AGE_HIGH': ('80 años o más.', 'Derivar a valoración médica.'),
    'Y01_HYPERTENSION': ('Hipertensión controlada.',
                         'Sin ejercicios con retención de la respiración; máximo 3 series; sin alimentos altos en sodio.'),
    'Y02_DIABETES_T2': ('Diabetes tipo 2 controlada o prediabetes.',
                        'Sin alimentos con azúcar libre alta; carbohidratos repartidos parejo; déficit máximo de 15 %.'),
    'Y03_THYROID_ASTHMA': ('Tiroides o asma controlados.',
                           'Intensidad máxima moderada en las primeras 2 semanas.'),
    'Y04_JOINT': ('Lesión articular, osteoporosis, hernia discal o problema óseo/articular.',
                  'Excluir ejercicios de la zona afectada y de alto impacto.'),
    'Y05_CLEARED': ('Cuestionario de aptitud con respuestas afirmativas y autorización médica vigente.',
                    'Revisión reforzada del entrenador.'),
    'Y06_ANTICOAGULANTS': ('Usa anticoagulantes.',
                           'Sin alto impacto. La dieta (vitamina K) debe revisarla un nutricionista.'),
    'Y07_SEVERE_ALLERGY': ('Alergia grave o enfermedad celíaca.',
                           'Exclusión estricta de trazas; el entrenador revisa cada alimento.'),
    'Y08_AGE_SENIOR': ('Entre 70 y 79 años.', 'Sin saltos ni cargas axiales pesadas; volumen bajo.'),
    'Y09_BMI_HIGH': ('IMC entre 35 y 39,9.', 'Sin alto impacto; progresión lenta.'),
    'Y10_ED_EXERCISE': ('Antecedentes de trastorno alimentario (afecta el ejercicio).',
                        'Sin cardio compensatorio adicional; sin meta de peso.'),
    'Y11_REHAB_GOAL': ('Meta de rehabilitación / salud general.',
                       'Requiere una lesión declarada; ejercicios solo de baja intensidad.'),
    'Y12_OTHER_TEXT': ('Texto libre en "otra condición" o "medicamentos".',
                       'El texto no se envía a la IA; el entrenador debe leerlo.'),
    'Y13_GI': ('Reflujo, colon irritable, gota o intolerancias.',
               'Se excluyen del catálogo los alimentos etiquetados para esa condición.'),
    'I01_SLEEP': ('Duerme menos de 5 horas.', 'Solo informativa.'),
    'I02_AGE_MISMATCH': ('La edad de la valoración inicial no coincide con la fecha de nacimiento (2 años o más).',
                         'Revisar cuál es la correcta.'),
    'M01_NO_MEASURE': ('No hay ninguna medición de peso.', 'La clienta debe registrar sus medidas.'),
    'M02_MEASURE_OLD': ('La última medición tiene más de 30 días.', 'La clienta debe registrar medidas nuevas.'),
    'M03_NO_HEIGHT': ('No hay estatura registrada.', 'Registrar la estatura en una medición.'),
    'M04_NO_PROFILE': ('La clienta aún no tiene ficha de salud.', 'La clienta debe llenar su ficha.'),
    'M05_NO_GOAL': ('No hay una meta de entrenamiento.', 'Definir la meta en la ficha.'),
    'M06_CLEARANCE_EXPIRED': ('La autorización médica declarada está vencida (12 meses o más).',
                              'Pedir una autorización nueva.'),
    'M07_NO_SLEEP': ('No se registraron las horas de sueño.', 'Opcional: preguntarlas.'),
    'M08_INCOHERENT': ('Datos incoherentes (IMC fuera de 8 a 100, o edad fuera de 18 a 100).',
                       'Corregir peso, estatura o fecha de nacimiento.'),
    'M09_NO_AI_CONSENT': ('La clienta no ha autorizado el envío sin identificar a la IA.',
                          'Pedirle la autorización (se puede armar el plan a mano sin ella).'),
}
_TEXTS['M04_PROFILE_UNCONFIRMED'] = (
    'La ficha fue corregida por el entrenador y la clienta aún no la confirma.',
    'La clienta debe confirmar su ficha.')


def _flag(code, severity, scopes=(), text=None, action=None):
    default_text, default_action = _TEXTS[code]
    return Flag(code=code, severity=severity, text=text or default_text,
                action=action if action is not None else default_action, scopes=tuple(scopes))


def _red(code, scopes):
    return _flag(code, SEVERITY_RED, scopes)


def _yellow(code, scopes):
    return _flag(code, SEVERITY_YELLOW, scopes)


E, N = SCOPE_EXERCISE, SCOPE_NUTRITION
BOTH = (E, N)
# Rojos de un solo alcance que dejan el otro alcance en amarillo (contrato 3.2)
_YELLOW_COMPANION = {'R03_EATING_DISORDER': E, 'R04_PARQ_SYMPTOMS': N,
                     'R08_BMI_VERY_HIGH': E, 'R10_KIDNEY': E}


# ─── Evaluación ───────────────────────────────────────────────────────────────

def evaluate(facts):
    """Aplica todas las reglas y devuelve un `FlagReport` (determinista, sin efectos)."""
    today = facts.today or date.today()
    red, yellow, info, missing = [], [], [], []

    value_bmi = bmi(facts)
    coherent = bmi_is_coherent(value_bmi)
    age_ok = facts.age is not None and ADULT_AGE <= facts.age <= MAX_REASONABLE_AGE

    # ── Datos faltantes ──
    if not facts.has_profile:
        missing.append(_flag('M04_NO_PROFILE', SEVERITY_MISSING, BOTH))
    elif not facts.profile_confirmed:
        missing.append(_flag('M04_PROFILE_UNCONFIRMED', SEVERITY_MISSING, BOTH))
    if facts.measurement_days_old is None:
        missing.append(_flag('M01_NO_MEASURE', SEVERITY_MISSING, BOTH))
    elif facts.measurement_days_old > MEASURE_MAX_DAYS:
        missing.append(_flag('M02_MEASURE_OLD', SEVERITY_MISSING, BOTH))
    if facts.height_m is None:
        missing.append(_flag('M03_NO_HEIGHT', SEVERITY_MISSING, BOTH))
    if not facts.ai_consent:
        missing.append(_flag('M09_NO_AI_CONSENT', SEVERITY_MISSING, BOTH))

    if not facts.has_profile:
        # Sin ficha no hay nada clínico que evaluar
        return _report(red, yellow, info, missing)

    if not facts.goal:
        missing.append(_flag('M05_NO_GOAL', SEVERITY_MISSING, BOTH))
    if facts.sleep_hours is None:
        missing.append(_flag('M07_NO_SLEEP', SEVERITY_MISSING))
    if (value_bmi is not None and not coherent) or (facts.age is not None and facts.age > MAX_REASONABLE_AGE):
        missing.append(_flag('M08_INCOHERENT', SEVERITY_MISSING, BOTH))

    conditions = set(facts.conditions)
    medications = set(facts.medications)
    clearance_valid = clearance_is_valid(facts.medical_clearance, facts.clearance_date, today)
    if facts.medical_clearance and not clearance_valid:
        missing.append(_flag('M06_CLEARANCE_EXPIRED', SEVERITY_MISSING, (E,)))
    bmi_value = value_bmi if coherent else None

    # ── Rojas ──
    if facts.age is not None and facts.age < ADULT_AGE:
        red.append(_red('R01_MINOR', BOTH))
    if facts.pregnancy_status != 'none':
        red.append(_red('R02_PREGNANCY', BOTH))
    eating_disorder = facts.eating_disorder_history in ('yes', 'prefer_not_say')
    if eating_disorder:
        red.append(_red('R03_EATING_DISORDER', (N,)))
    if facts.parq_yes('q2', 'q3', 'q4'):
        red.append(_red('R04_PARQ_SYMPTOMS', (E,)))
    if facts.parq_yes('q1') or conditions & set(CARDIAC_CONDITIONS):
        red.append(_red('R05_CARDIAC', BOTH))
    needs_clearance = facts.parq_yes('q5', 'q6', 'q7')
    if needs_clearance and not clearance_valid:
        red.append(_red('R06_NEEDS_CLEARANCE', (E,)))
    if bmi_value is not None:
        if bmi_value < BMI_VERY_LOW:
            red.append(_red('R07_BMI_LOW', BOTH))
        elif bmi_value < BMI_LOW and facts.goal in GOALS_REDUCING:
            red.append(_red('R07_BMI_LOW', (N,)))
        if bmi_value >= BMI_VERY_HIGH:
            red.append(_red('R08_BMI_VERY_HIGH', (N,)))
    if 'diabetes_t1' in conditions or medications & set(T1_MEDICATIONS):
        red.append(_red('R09_DIABETES_T1_INSULIN', BOTH))
    if 'kidney_disease' in conditions:
        red.append(_red('R10_KIDNEY', (N,)))
    if conditions & set(LIVER_METABOLIC_CONDITIONS):
        red.append(_red('R11_LIVER_METABOLIC', (N,)))
    if 'cancer_active' in conditions:
        red.append(_red('R12_CANCER', BOTH))
    if facts.recent_surgery == 'under_6m':
        red.append(_red('R13_RECENT_SURGERY', (E,)))
    intermediate = conditions & set(INTERMEDIATE_CONDITIONS)
    controlled_ok = bool(facts.condition_controlled and clearance_valid)
    if intermediate and not controlled_ok:
        red.append(_red('R14_NOT_CONTROLLED', BOTH if 'diabetes_t2' in intermediate else (E,)))
    if facts.age is not None and facts.age >= VERY_OLD_AGE:
        red.append(_red('R15_AGE_HIGH', (E,)))

    # ── Amarillas ──
    if 'hypertension' in conditions and controlled_ok:
        yellow.append(_yellow('Y01_HYPERTENSION', BOTH))
    if ('diabetes_t2' in conditions and controlled_ok) or 'prediabetes' in conditions:
        yellow.append(_yellow('Y02_DIABETES_T2', (N,)))
    if conditions & {'thyroid_disease', 'asthma'} and controlled_ok:
        yellow.append(_yellow('Y03_THYROID_ASTHMA', (E,)))
    if facts.injuries or conditions & set(JOINT_CONDITIONS) or facts.parq_yes('q5'):
        yellow.append(_yellow('Y04_JOINT', (E,)))
    if needs_clearance and clearance_valid:
        yellow.append(_yellow('Y05_CLEARED', (E,)))
    if 'anticoagulants' in medications:
        yellow.append(_yellow('Y06_ANTICOAGULANTS', BOTH))
    if (facts.allergies and facts.allergy_severity == 'anaphylaxis') or 'celiac' in conditions:
        yellow.append(_yellow('Y07_SEVERE_ALLERGY', (N,)))
    if facts.age is not None and SENIOR_AGE <= facts.age < VERY_OLD_AGE:
        yellow.append(_yellow('Y08_AGE_SENIOR', (E,)))
    if bmi_value is not None and BMI_HIGH <= bmi_value < BMI_VERY_HIGH:
        yellow.append(_yellow('Y09_BMI_HIGH', (E,)))
    if eating_disorder:
        yellow.append(_yellow('Y10_ED_EXERCISE', (E,)))
    if facts.goal == 'rehab':
        text = None
        if not facts.injuries:
            text = 'Meta de rehabilitación sin lesión declarada: confirmar con la clienta.'
        yellow.append(_flag('Y11_REHAB_GOAL', SEVERITY_YELLOW, (E,), text=text))
    if 'other' in conditions or 'other' in medications or facts.has_other_text:
        yellow.append(_yellow('Y12_OTHER_TEXT', BOTH))
    if conditions & set(GI_CONDITIONS) or facts.intolerances:
        yellow.append(_yellow('Y13_GI', (N,)))

    # ── Informativas ──
    if facts.sleep_hours is not None and facts.sleep_hours < SLEEP_LOW_HOURS:
        info.append(_flag('I01_SLEEP', SEVERITY_INFO))
    if (facts.assessment_age is not None and facts.age is not None
            and abs(facts.assessment_age - facts.age) >= AGE_MISMATCH_YEARS):
        info.append(_flag('I02_AGE_MISMATCH', SEVERITY_INFO))

    return _report(red, yellow, info, missing)


def _report(red, yellow, info, missing):
    blocked = set()
    for flag in red:
        blocked.update(flag.scopes)
    yellow_scopes = set()
    for flag in yellow:
        yellow_scopes.update(flag.scopes)
    for flag in red:
        companion = _YELLOW_COMPANION.get(flag.code)
        if companion is not None:
            yellow_scopes.add(companion)
    yellow_scopes -= blocked
    return FlagReport(
        red=tuple(red), yellow=tuple(yellow), info=tuple(info), missing=tuple(missing),
        blocked_scopes=frozenset(blocked), yellow_scopes=frozenset(yellow_scopes),
    )
