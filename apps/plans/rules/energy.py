"""Cálculo energético y reparto de macros (contrato, sección 3.4). La IA NO inventa cifras: todo
sale de aquí, con `Decimal`.

Módulo PURO (sin Django ni red).

[A VALIDAR] por un nutricionista-dietista: la fórmula (Mifflin-St Jeor), los factores de actividad,
los déficits y superávits, los pisos calóricos, los gramos de proteína por kilo y el reparto por
comida. Es una propuesta técnica, no una prescripción.
"""
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

FORMULA = 'mifflin_st_jeor/1'

D = Decimal
ACTIVITY_FACTORS = {'sedentary': D('1.2'), 'light': D('1.375'), 'moderate': D('1.55'), 'active': D('1.725')}
SEX_CONSTANT = {'M': D('5'), 'F': D('-161'), 'NA': D('-78')}
KCAL_FLOOR = {'F': D('1200'), 'M': D('1500'), 'NA': D('1350')}

DEFICIT_PCT_LOSS = D('0.20')            # bajar de peso: menor entre 20 % del GET y 500 kcal
DEFICIT_PCT_LOSS_DIABETES = D('0.15')   # Y02: sin déficit mayor a 15 %
DEFICIT_MAX_KCAL = D('500')
DEFICIT_PCT_TONING = D('0.10')          # tonificar con IMC >= 25: menor entre 10 % y 300 kcal
DEFICIT_MAX_KCAL_TONING = D('300')
SURPLUS_PCT = D('0.10')                 # ganar músculo: menor entre 10 % y 300 kcal
SURPLUS_MAX_KCAL = D('300')
TONING_BMI = D('25')
MAX_LOSS_KG_PER_WEEK = D('0.5')
KCAL_PER_KG = D('7700')
TARGET_BMI_MIN = D('20')                # peso meta que implique IMC menor se marca
REFERENCE_BMI = D('27')                 # peso de referencia para la proteína = min(peso, peso con IMC 27)

PROTEIN_G_PER_KG = {'weight_loss': D('1.6'), 'muscle_gain': D('1.8')}
PROTEIN_G_PER_KG_DEFAULT = D('1.2')
PROTEIN_MAX_G_PER_KG = D('2.0')
PROTEIN_MAX_ENERGY_SHARE = D('0.35')
FAT_ENERGY_SHARE = D('0.28')            # mínimo 20 % de la energía y 0,6 g/kg
FAT_MIN_G_PER_KG = D('0.6')
FIBER_MIN_G = D('25')
FIBER_G_PER_1000_KCAL = D('14')
LOW_CARBS_SHARE = D('0.15')

# Advertencias del cálculo (para el entrenador; no son diagnósticos)
W_NO_DEFICIT = 'NO_DEFICIT_APPLIED'     # el piso calórico supera el gasto: no se aplicó déficit
W_FLOOR = 'FLOOR_APPLIED'               # el déficit se recortó al piso calórico
W_TARGET_WEIGHT_LOW = 'TARGET_WEIGHT_LOW'
W_LOW_CARBS = 'LOW_CARBS'
WARNING_TEXTS = {
    W_NO_DEFICIT: 'No se aplicó déficit: el piso calórico supera el gasto estimado.',
    W_FLOOR: 'El objetivo se subió al piso calórico mínimo.',
    W_TARGET_WEIGHT_LOW: 'El peso meta implicaría un IMC menor de 20: el plan no persigue ese peso.',
    W_LOW_CARBS: 'Los carbohidratos quedan por debajo del 15 % de la energía: revisa el reparto.',
}

SLOT_NAMES = {
    'breakfast': 'Desayuno', 'mid_morning': 'Media mañana', 'lunch': 'Almuerzo',
    'snack': 'Merienda', 'dinner': 'Cena',
}
SLOT_ORDER = ('breakfast', 'mid_morning', 'lunch', 'snack', 'dinner')
# Porcentaje de la energía del día por comida (3.4). 4 comidas con media mañana y sin merienda:
# propuesta 25/10/35/30 [A VALIDAR].
MEAL_SPLITS = {
    ('breakfast', 'lunch', 'dinner'): (30, 40, 30),
    ('breakfast', 'lunch', 'snack', 'dinner'): (25, 35, 15, 25),
    ('breakfast', 'mid_morning', 'lunch', 'dinner'): (25, 10, 35, 30),
    ('breakfast', 'mid_morning', 'lunch', 'snack', 'dinner'): (25, 10, 30, 10, 25),
}


def _round(value, step=D('1')):
    return (D(value) / step).quantize(D('1'), rounding=ROUND_HALF_UP) * step


def _round_up(value, step=D('1')):
    return (D(value) / step).quantize(D('1'), rounding=ROUND_CEILING) * step


@dataclass(frozen=True)
class EnergyInput:
    """Datos del cálculo (ya como números; sin objetos de Django)."""
    age: int
    sex: str                    # 'F' | 'M' | 'NA'
    weight_kg: Decimal
    height_m: Decimal
    activity_level: str         # sedentary | light | moderate | active
    goal: str = ''
    diabetes_cap: bool = False  # Y02: déficit máximo de 15 %
    target_weight_kg: Decimal | None = None


@dataclass(frozen=True)
class EnergyPlan:
    tmb: Decimal
    get: Decimal
    activity_factor: Decimal
    adjustment_kcal: Decimal    # negativo = déficit
    adjustment_pct: Decimal     # porcentaje del GET, con signo
    kcal: Decimal               # objetivo diario
    protein_g: Decimal
    carbs_g: Decimal
    fat_g: Decimal
    fiber_g: Decimal
    floor_applied: bool
    warnings: tuple = ()
    weekly_change_kg: Decimal = D('0')
    formula: str = FORMULA

    @property
    def warning_texts(self):
        return [WARNING_TEXTS[code] for code in self.warnings if code in WARNING_TEXTS]


def bmi(weight_kg, height_m):
    return D(weight_kg) / (D(height_m) ** 2)


def compute_energy(data):
    """Objetivo diario de energía y macros. Lanza `ValueError` con datos imposibles."""
    if data.sex not in SEX_CONSTANT or data.activity_level not in ACTIVITY_FACTORS:
        raise ValueError('sexo o nivel de actividad desconocido')
    weight, height_m = D(data.weight_kg), D(data.height_m)
    if weight <= 0 or height_m <= 0:
        raise ValueError('peso o estatura inválidos')
    height_cm = height_m * 100
    tmb = D(10) * weight + D('6.25') * height_cm - D(5) * D(data.age) + SEX_CONSTANT[data.sex]
    factor = ACTIVITY_FACTORS[data.activity_level]
    get = tmb * factor
    current_bmi = bmi(weight, height_m)

    adjustment = D('0')
    if data.goal == 'weight_loss':
        pct = DEFICIT_PCT_LOSS_DIABETES if data.diabetes_cap else DEFICIT_PCT_LOSS
        adjustment = -min(pct * get, DEFICIT_MAX_KCAL)
    elif data.goal == 'toning' and current_bmi >= TONING_BMI:
        adjustment = -min(DEFICIT_PCT_TONING * get, DEFICIT_MAX_KCAL_TONING)
    elif data.goal == 'muscle_gain':
        adjustment = min(SURPLUS_PCT * get, SURPLUS_MAX_KCAL)

    warnings = []
    floor = KCAL_FLOOR[data.sex]
    target = get + adjustment
    floor_applied = False
    if target < floor:
        if floor > get:
            # El piso supera el gasto: mantenimiento, sin déficit (y se avisa)
            if adjustment < 0:
                warnings.append(W_NO_DEFICIT)
            target, adjustment = get, D('0')
        else:
            target, adjustment, floor_applied = floor, floor - get, True
            warnings.append(W_FLOOR)
    # Con déficit el redondeo sube (nunca queda un déficit más profundo que el tope)
    kcal = _round_up(target, D('10')) if adjustment < 0 else _round(target, D('10'))
    adjustment_kcal = kcal - _round(get, D('10')) if adjustment else D('0')
    weekly_change = (adjustment_kcal * 7 / KCAL_PER_KG).quantize(D('0.01'))
    if weekly_change < -MAX_LOSS_KG_PER_WEEK:    # no ocurre con los topes de arriba; defensa
        raise ValueError('ritmo de pérdida mayor al permitido')

    if data.target_weight_kg:
        if bmi(data.target_weight_kg, height_m) < TARGET_BMI_MIN:
            warnings.append(W_TARGET_WEIGHT_LOW)

    reference_weight = min(weight, REFERENCE_BMI * height_m ** 2)
    per_kg = PROTEIN_G_PER_KG.get(data.goal, PROTEIN_G_PER_KG_DEFAULT)
    protein = min(per_kg * reference_weight, PROTEIN_MAX_G_PER_KG * reference_weight,
                  PROTEIN_MAX_ENERGY_SHARE * kcal / 4)
    fat = max(FAT_ENERGY_SHARE * kcal / 9, FAT_MIN_G_PER_KG * weight)
    carbs = max((kcal - 4 * protein - 9 * fat) / 4, D('0'))
    if carbs * 4 < LOW_CARBS_SHARE * kcal:
        warnings.append(W_LOW_CARBS)
    fiber = max(FIBER_MIN_G, FIBER_G_PER_1000_KCAL * kcal / 1000)
    return EnergyPlan(
        tmb=_round(tmb, D('0.1')), get=_round(get, D('10')), activity_factor=factor,
        adjustment_kcal=adjustment_kcal,
        adjustment_pct=(adjustment / get * 100).quantize(D('0.1')) if adjustment else D('0.0'),
        kcal=kcal, protein_g=_round(protein), carbs_g=_round(carbs), fat_g=_round(fat), fiber_g=_round(fiber),
        floor_applied=floor_applied, warnings=tuple(warnings), weekly_change_kg=weekly_change,
    )


@dataclass(frozen=True)
class SlotTarget:
    code: str
    name: str
    order: int
    percent: int
    kcal: Decimal
    protein_g: Decimal
    carbs_g: Decimal
    fat_g: Decimal


def canonical_slots(meal_slots):
    """Comidas de la ficha en el orden del día. Lanza `ValueError` si no es un reparto conocido."""
    chosen = tuple(code for code in SLOT_ORDER if code in set(meal_slots or ()))
    if chosen not in MEAL_SPLITS:
        raise ValueError('reparto de comidas no soportado')
    return chosen


def split_meals(energy, meal_slots):
    """Energía y macros de cada comida (kcal a 10, gramos a 1)."""
    codes = canonical_slots(meal_slots)
    result = []
    for order, (code, percent) in enumerate(zip(codes, MEAL_SPLITS[codes])):
        share = D(percent) / 100
        result.append(SlotTarget(
            code=code, name=SLOT_NAMES[code], order=order, percent=percent,
            kcal=_round(energy.kcal * share, D('10')), protein_g=_round(energy.protein_g * share),
            carbs_g=_round(energy.carbs_g * share), fat_g=_round(energy.fat_g * share),
        ))
    return result
