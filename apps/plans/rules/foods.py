"""Catálogo de alimentos para el plan de alimentación: filtro ANTES de llamar a la IA (contrato,
sección 3.5), cálculo de macros, medidas caseras e intercambios.

Módulo PURO (sin Django ni red). La IA solo recibe los alimentos permitidos y elige ids y gramos;
las kcal, los macros y las medidas caseras los calcula este módulo desde la tabla `Food`.

[A VALIDAR] por un nutricionista-dietista: qué etiquetas excluye cada condición y las porciones de
intercambio.
"""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

D = Decimal

GROUPS = [
    ('cereales', 'Cereales y derivados'),
    ('tuberculos', 'Tubérculos y plátanos'),
    ('verduras', 'Verduras'),
    ('frutas', 'Frutas'),
    ('lacteos', 'Lácteos y alternativas'),
    ('proteina_animal', 'Carnes, pescados y huevo'),
    ('leguminosas', 'Leguminosas'),
    ('grasas', 'Grasas, nueces y semillas'),
    ('azucares', 'Azúcares y dulces'),
    ('bebidas', 'Bebidas'),
]
GROUP_LABELS = dict(GROUPS)
GROUP_ORDER = {code: index for index, (code, _label) in enumerate(GROUPS)}

ALLERGEN_CODES = ('gluten', 'milk', 'egg', 'peanut', 'tree_nut', 'fish', 'shellfish', 'soy', 'sesame')
DIET_CODES = ('omnivore', 'vegetarian', 'vegan', 'pescatarian', 'halal', 'kosher')
# Etiquetas de condición de los alimentos (las usan las advertencias Y01, Y02 y Y13)
CONDITION_TAGS = (
    ('high_sodium', 'Alto en sodio'),
    ('high_free_sugar', 'Azúcar libre alta'),
    ('fodmap', 'Alto en FODMAP'),
    ('high_purine', 'Alto en purinas'),
    ('reflux_trigger', 'Puede irritar (reflujo/gastritis)'),
)
# Advertencia o condición de la ficha -> etiqueta de alimento excluida
_FLAG_TO_TAG = {'Y01_HYPERTENSION': 'high_sodium', 'Y02_DIABETES_T2': 'high_free_sugar'}
_CONDITION_TO_TAG = {'gout': 'high_purine', 'reflux_gastritis': 'reflux_trigger', 'ibs': 'fodmap',
                     'prediabetes': 'high_free_sugar'}
_INTOLERANCE_TO_TAG = {'fructose': 'fodmap', 'fodmap': 'fodmap'}

MIN_ALLOWED_FOODS = 40            # menos que esto: D06_FEW_FOODS
MIN_PROTEIN_SOURCES = 3           # alimentos con 10 g de proteína o más por 100 g
MIN_CARB_SOURCES = 3              # alimentos con 15 g de carbohidratos o más por 100 g
PROTEIN_SOURCE_P100 = D('10')
CARB_SOURCE_C100 = D('15')

# Porción de intercambio (kcal) por grupo [A VALIDAR]
EXCHANGE_KCAL = {
    'cereales': 100, 'tuberculos': 100, 'verduras': 25, 'frutas': 60,
    'lacteos': 100, 'proteina_animal': 100, 'leguminosas': 100, 'grasas': 45,
}


@dataclass(frozen=True)
class FoodItem:
    """Lo mínimo de un `Food` (sin objetos de Django). Valores por 100 g."""
    id: int
    name: str
    group: str
    kcal100: Decimal
    protein100: Decimal
    carbs100: Decimal
    fat100: Decimal
    fiber100: Decimal = D('0')
    household_unit: str = ''
    household_unit_plural: str = ''
    household_grams: Decimal | None = None
    allergen_tags: tuple = ()
    trace_tags: tuple = ()
    diet_tags: tuple = DIET_CODES
    condition_tags: tuple = ()
    is_common: bool = True


@dataclass(frozen=True)
class NutritionProfile:
    """Lo de la ficha que afecta el filtro (alergias y condiciones se usan aquí, en el servidor,
    y nunca viajan a la IA)."""
    diet: str = 'omnivore'
    allergies: tuple = ()
    intolerances: tuple = ()
    conditions: tuple = ()
    yellow_codes: tuple = ()


@dataclass(frozen=True)
class FoodCatalog:
    allowed: tuple

    @property
    def allowed_ids(self):
        return frozenset(item.id for item in self.allowed)

    @property
    def by_id(self):
        return {item.id: item for item in self.allowed}

    @property
    def protein_sources(self):
        return sum(1 for item in self.allowed if item.protein100 >= PROTEIN_SOURCE_P100)

    @property
    def carb_sources(self):
        return sum(1 for item in self.allowed if item.carbs100 >= CARB_SOURCE_C100)

    @property
    def enough(self):
        return (len(self.allowed) >= MIN_ALLOWED_FOODS and self.protein_sources >= MIN_PROTEIN_SOURCES
                and self.carb_sources >= MIN_CARB_SOURCES)


def excluded_allergens(profile):
    """Alérgenos que la clienta debe evitar (alergias y celiaquía)."""
    allergens = set(profile.allergies)
    if 'celiac' in profile.conditions:
        allergens.add('gluten')
    if 'lactose' in profile.intolerances:
        allergens.add('milk')
    return allergens


def excluded_condition_tags(profile):
    tags = {tag for code, tag in _FLAG_TO_TAG.items() if code in profile.yellow_codes}
    tags |= {tag for condition, tag in _CONDITION_TO_TAG.items() if condition in profile.conditions}
    tags |= {tag for name, tag in _INTOLERANCE_TO_TAG.items() if name in profile.intolerances}
    return tags


def filter_foods(items, profile):
    """Alimentos permitidos para la clienta (alérgenos y trazas, dieta, intolerancias y etiquetas
    de las advertencias)."""
    allergens = excluded_allergens(profile)
    tags = excluded_condition_tags(profile)
    allowed = []
    for item in items:
        if allergens & (set(item.allergen_tags) | set(item.trace_tags)):
            continue
        if tags & set(item.condition_tags):
            continue
        if profile.diet in DIET_CODES and profile.diet not in item.diet_tags:
            continue
        allowed.append(item)
    allowed.sort(key=lambda item: (GROUP_ORDER.get(item.group, 99), item.name, item.id))
    return FoodCatalog(allowed=tuple(allowed))


# ─── Macros de una porción ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Totals:
    kcal: Decimal = D('0')
    protein: Decimal = D('0')
    carbs: Decimal = D('0')
    fat: Decimal = D('0')
    fiber: Decimal = D('0')

    def __add__(self, other):
        return Totals(self.kcal + other.kcal, self.protein + other.protein, self.carbs + other.carbs,
                      self.fat + other.fat, self.fiber + other.fiber)

    @property
    def fat_share(self):
        return (self.fat * 9 / self.kcal) if self.kcal else D('0')


def portion_totals(food, grams):
    factor = D(grams) / 100
    return Totals(food.kcal100 * factor, food.protein100 * factor, food.carbs100 * factor,
                  food.fat100 * factor, food.fiber100 * factor)


def items_totals(rows):
    """`rows` = [(FoodItem, gramos)] -> `Totals`."""
    total = Totals()
    for food, grams in rows:
        total = total + portion_totals(food, grams)
    return total


def round_totals(total):
    """Para mostrar: kcal a 5 y gramos a 1 decimal."""
    return {
        'kcal': int((total.kcal / 5).quantize(D('1'), rounding=ROUND_HALF_UP) * 5),
        'protein': float(total.protein.quantize(D('0.1'), rounding=ROUND_HALF_UP)),
        'carbs': float(total.carbs.quantize(D('0.1'), rounding=ROUND_HALF_UP)),
        'fat': float(total.fat.quantize(D('0.1'), rounding=ROUND_HALF_UP)),
        'fiber': float(total.fiber.quantize(D('0.1'), rounding=ROUND_HALF_UP)),
    }


# ─── Medidas caseras ──────────────────────────────────────────────────────────

_FRACTIONS = {0: '', 1: '¼', 2: '½', 3: '¾'}


def household_text(food, grams):
    """"1 ½ tazas cocidas (210 g)": la medida casera se calcula, no la escribe la IA."""
    grams = int(D(grams).quantize(D('1'), rounding=ROUND_HALF_UP))
    if not food.household_grams or not food.household_unit:
        return f'{grams} g'
    quarters = max(1, int((D(grams) / food.household_grams * 4).quantize(D('1'), rounding=ROUND_HALF_UP)))
    whole, rest = divmod(quarters, 4)
    quantity = ' '.join(part for part in (str(whole) if whole else '', _FRACTIONS[rest]) if part)
    unit = food.household_unit if quarters <= 4 else (food.household_unit_plural or food.household_unit)
    return f'{quantity} {unit} ({grams} g)'


# ─── Intercambios ─────────────────────────────────────────────────────────────

def _round_grams(value):
    step = D('5') if value >= 20 else D('1')
    return max(step, (value / step).quantize(D('1'), rounding=ROUND_HALF_UP) * step)


def exchange_groups(items):
    """Equivalencias por grupo con los alimentos permitidos: cada alimento con los gramos y la
    medida casera de UNA porción de intercambio del grupo (misma energía)."""
    by_group = {}
    for item in items:
        if item.group in EXCHANGE_KCAL and item.kcal100 > 0:
            by_group.setdefault(item.group, []).append(item)
    result = []
    for code, label in GROUPS:
        foods = by_group.get(code)
        if not foods:
            continue
        portion = D(EXCHANGE_KCAL[code])
        rows = []
        for food in sorted(foods, key=lambda f: (not f.is_common, f.name)):
            grams = _round_grams(portion / food.kcal100 * 100)
            if grams > 600:
                continue
            rows.append({'food': food, 'grams': int(grams), 'household': household_text(food, grams)})
        if rows:
            result.append({'group': code, 'label': label, 'portion_kcal': int(portion), 'rows': rows})
    return result


@dataclass
class ShoppingLine:
    food: FoodItem
    grams: int = 0
    days: set = field(default_factory=set)
