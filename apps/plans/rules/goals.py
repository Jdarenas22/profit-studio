"""Preferencias nutricionales por objetivo, medidas y ficha para ORDENAR las recetas (sin IA, puro y determinista).

Qué decide cada cosa (no se mezclan):
- Cuántas calorías y cuánta proteína lleva cada comida: `energy.py` (objetivo, peso, estatura, edad, actividad).
- Qué alimentos puede comer la persona: filtros de dieta, alergias, intolerancias y condiciones (`foods.py`, `flags.py`).
- Qué recetas se prefieren entre las que ya cumplen todo lo anterior: este módulo. Nunca añade ni quita alimentos
  ni cambia cantidades: solo puntúa lo que ya es válido.

Cada objetivo declara qué rasgos de un plato favorece. Los rasgos se calculan sobre las porciones ya ajustadas:
- proteína y carbohidratos como porcentaje de la energía del plato,
- fibra por cada 100 kcal (saciedad),
- densidad energética (kcal por 100 g de plato: menos densidad = más volumen por las mismas calorías),
- grasa saturada como porcentaje de la energía, sodio por cada 100 kcal y azúcares añadidos,
- cuántos ingredientes son «comunes y de fácil acceso» (si el presupuesto de la ficha es bajo).

Los umbrales son criterios generales de alimentación saludable y están marcados [A VALIDAR] por un nutricionista
(igual que los de `flags.py` y `energy.py`). Son preferencias, no prescripciones.
"""
from dataclasses import dataclass
from decimal import Decimal

D = Decimal

GOAL_WEIGHT_LOSS = 'weight_loss'
GOAL_MUSCLE_GAIN = 'muscle_gain'
GOAL_CARDIO = 'cardio'
GOAL_REHAB = 'rehab'
GOAL_SPORTS = 'sports'
GOAL_TONING = 'toning'

# rasgo -> (valor que cuenta como «nada», valor que cuenta como «todo»); el puntaje de cada rasgo va de 0 a 1.
RANGES = {
    'protein_share': (0.15, 0.35),         # fracción de la energía que aporta la proteína
    'carb_share': (0.30, 0.65),            # fracción de la energía que aportan los carbohidratos
    'fat_share': (0.20, 0.45),
    'fiber_density': (0.5, 3.5),           # g de fibra por 100 kcal
    'energy_density': (60.0, 220.0),       # kcal por 100 g de plato
    'sat_fat_share': (0.04, 0.14),         # fracción de la energía como grasa saturada
    'sodium_density': (30.0, 300.0),       # mg de sodio por 100 kcal
    'added_sugars': (0.0, 12.0),           # g de azúcares añadidos en el plato
    'common_share': (0.5, 1.0),            # fracción de ingredientes comunes y de fácil acceso
}

# objetivo -> {rasgo: peso}. Positivo = se prefiere alto; negativo = se prefiere bajo.
GOAL_PROFILES = {
    # Pérdida de peso: saciedad (proteína, fibra, volumen) con pocas calorías vacías.
    GOAL_WEIGHT_LOSS: {'protein_share': 1.0, 'fiber_density': 0.8, 'energy_density': -0.8,
                       'added_sugars': -0.6, 'sat_fat_share': -0.4},
    # Tonificación y definición: proteína alta y platos ligeros.
    GOAL_TONING: {'protein_share': 1.0, 'fiber_density': 0.5, 'energy_density': -0.4, 'added_sugars': -0.4},
    # Ganancia muscular: proteína y energía suficientes (platos algo densos son una ventaja, no un problema).
    GOAL_MUSCLE_GAIN: {'protein_share': 0.8, 'carb_share': 0.5, 'energy_density': 0.3, 'added_sugars': -0.2},
    # Resistencia cardiovascular: carbohidratos para el esfuerzo y cuidado del corazón (poca grasa saturada y sodio).
    GOAL_CARDIO: {'carb_share': 0.8, 'fiber_density': 0.6, 'sat_fat_share': -0.7, 'sodium_density': -0.6},
    # Rendimiento deportivo: carbohidratos primero, proteína moderada, platos no demasiado grasos.
    GOAL_SPORTS: {'carb_share': 1.0, 'protein_share': 0.4, 'fat_share': -0.3},
    # Rehabilitación / salud general: fibra y poca grasa saturada, sodio y azúcar.
    GOAL_REHAB: {'fiber_density': 0.7, 'sat_fat_share': -0.6, 'sodium_density': -0.6, 'added_sugars': -0.5,
                 'energy_density': -0.2},
}
SATIETY_FEATURES = ('fiber_density', 'energy_density')
# Con IMC alto se refuerza la saciedad; con IMC bajo se favorece la densidad energética y la proteína.
BMI_HIGH, BMI_LOW = D('30'), D('18.5')
BMI_SATIETY_BOOST = 1.25
LOW_BUDGET_COMMON_WEIGHT = 0.5
BUDGETS_LOW = ('low',)


@dataclass(frozen=True)
class GoalContext:
    """Lo que se sabe de la persona para ordenar recetas (sin identificarla)."""
    goal: str = ''
    bmi: Decimal | None = None
    budget: str = ''                # presupuesto de la ficha: low | medium | high
    activity_level: str = ''

    @property
    def active(self):
        return bool(self.goal in GOAL_PROFILES or self.budget in BUDGETS_LOW)


def bmi_of(weight_kg, height_m):
    """IMC o `None` si falta un dato o es imposible."""
    try:
        weight, height = D(weight_kg), D(height_m)
    except Exception:
        return None
    if weight <= 0 or height <= 0:
        return None
    return weight / (height * height)


def recipe_metrics(items, foods):
    """Rasgos de un plato. `items` = [{'food_id', 'grams'}]; `foods` = id -> FoodItem. Un rasgo es `None` si algún
    ingrediente no trae el dato (no se inventa un cero)."""
    kcal = protein = carbs = fat = grams = D('0')
    fiber, saturated, sodium, added = D('0'), D('0'), D('0'), D('0')
    unknown = set()
    common = total = 0
    for item in items:
        food = foods.get(item['food_id'])
        if food is None:
            return None
        weight = D(item['grams'])
        factor = weight / 100
        grams += weight
        kcal += food.kcal100 * factor
        protein += food.protein100 * factor
        carbs += food.carbs100 * factor
        fat += food.fat100 * factor
        for label, value in (('fiber', food.fiber100), ('sat', food.saturated_fat100),
                             ('sodium', food.sodium_mg100), ('added', food.added_sugars100)):
            if value is None:
                unknown.add(label)
        fiber += (food.fiber100 or D('0')) * factor
        saturated += (food.saturated_fat100 or D('0')) * factor
        sodium += (food.sodium_mg100 or D('0')) * factor
        added += (food.added_sugars100 or D('0')) * factor
        total += 1
        common += 1 if food.is_common else 0
    if kcal <= 0 or grams <= 0:
        return None
    kcal_f = float(kcal)
    return {
        'protein_share': float(protein * 4) / kcal_f,
        'carb_share': float(carbs * 4) / kcal_f,
        'fat_share': float(fat * 9) / kcal_f,
        'energy_density': kcal_f / float(grams) * 100,
        'fiber_density': None if 'fiber' in unknown else float(fiber) / kcal_f * 100,
        'sat_fat_share': None if 'sat' in unknown else float(saturated * 9) / kcal_f,
        'sodium_density': None if 'sodium' in unknown else float(sodium) / kcal_f * 100,
        'added_sugars': None if 'added' in unknown else float(added),
        'common_share': common / total if total else None,
    }


def _ramp(value, low, high):
    if high == low:
        return 0.0
    return min(1.0, max(0.0, (value - low) / (high - low)))


def _weights(context):
    weights = dict(GOAL_PROFILES.get(context.goal, {}))
    bmi = context.bmi
    if bmi is not None and weights:
        if bmi >= BMI_HIGH and context.goal not in (GOAL_MUSCLE_GAIN, GOAL_SPORTS):
            for feature in SATIETY_FEATURES:
                if feature in weights:
                    weights[feature] *= BMI_SATIETY_BOOST
        elif bmi < BMI_LOW:
            weights['energy_density'] = abs(weights.get('energy_density', 0.3))      # con bajo peso no se premia lo ligero
            weights['protein_share'] = max(weights.get('protein_share', 0.0), 0.5)
    if context.budget in BUDGETS_LOW:
        weights['common_share'] = LOW_BUDGET_COMMON_WEIGHT
    return weights


def score_recipe(metrics, context):
    """De -1 (lo que menos conviene) a +1 (lo que más conviene) para este objetivo. 0 si no hay criterio."""
    if metrics is None:
        return 0.0
    weights = _weights(context)
    total = used = 0.0
    for feature, weight in weights.items():
        value = metrics.get(feature)
        if value is None:
            continue                                  # dato desconocido: ni premia ni castiga
        low, high = RANGES[feature]
        normalized = _ramp(value, low, high)
        # peso positivo: cuanto más alto el rasgo, mejor; negativo: cuanto más bajo, mejor
        total += abs(weight) * (normalized if weight > 0 else 1.0 - normalized)
        used += abs(weight)
    if not used:
        return 0.0
    # Cada rasgo aporta entre 0 y su peso; se lleva a la escala -1..+1 alrededor del punto medio
    return (2.0 * total / used) - 1.0


def score_options(options, foods, context):
    """Puntaje por nombre de receta para ORDENAR las ofrecidas. Sin objetivo ni presupuesto bajo: `{}` (orden neutro)."""
    if context is None or not context.active:
        return {}
    return {option['name']: round(score_recipe(recipe_metrics(option['items'], foods), context), 6)
            for option in options}
