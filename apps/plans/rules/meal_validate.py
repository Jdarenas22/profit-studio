"""Validación de la salida de la IA para una comida (`meal-slot/1`; contrato, sección 3.6): nada sin
validar llega a pantalla.

Módulo PURO (sin Django ni red). La IA elige alimentos del catálogo permitido y gramos de
orientación; aquí se comprueba el esquema exacto (`V01`), que todo id esté entre los permitidos
(`V02`), que el texto no nombre un alérgeno ni un alimento que la dieta excluye (`V03`, segunda
capa sobre texto libre), que las porciones —ajustadas automáticamente— queden en la banda de
energía (`V04`), los macros mínimos (`V05`), la variedad entre opciones (`V06`) y el texto (`V08`).

Si algo falla se devuelven CÓDIGOS, nunca el contenido.
"""
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from . import foods as f
from .validate import (
    V01_SCHEMA, V02_IDS, ValidationResult, _exact_keys, _is_int, check_text, normalize_for_search,
)

D = Decimal

MEAL_SCHEMA_VERSION = 'meal-slot/1'
V03_ALLERGEN = 'V03_ALLERGEN'
V04_KCAL = 'V04_KCAL'
V05_MACROS = 'V05_MACROS'
V06_VARIETY = 'V06_VARIETY'

OPTIONS_PER_SLOT = 3
MIN_ITEMS, MAX_ITEMS = 2, 6
MIN_GRAMS, MAX_GRAMS = 5, 600
MAX_NAME, MAX_PREPARATION = 100, 400            # MealOption.name admite 120
SCALE_MIN, SCALE_MAX = D('0.7'), D('1.3')
KCAL_TOLERANCE = D('0.05')
PROTEIN_MIN_SHARE = D('0.90')
FAT_MIN_SHARE = D('0.20')
VARIETY_MAX_OVERLAP = D('0.5')                  # Jaccard de alimentos: menos de 50 %

_TOP_KEYS = {'schema_version', 'slot_code', 'options'}
_OPTION_KEYS = {'name', 'items', 'preparation'}
_ITEM_KEYS = {'food_id', 'grams'}


# ─── Segunda capa: palabras sobre el texto libre ──────────────────────────────

def _words(*groups):
    return [word for group in groups for word in group]


# Sin tildes y en minúsculas. Una palabra que termina en `$` debe coincidir completa; las demás
# coinciden como prefijo ("camaron" -> "camarones"). Es conservador a propósito.
_MILK = ['leche', 'lacteo', 'queso', 'yogur', 'kumis', 'crema', 'mantequilla', 'suero', 'cuajada',
         'kefir', 'requeson', 'nata$', 'natilla', 'helado', 'ricotta', 'mozzarella', 'parmesano',
         'paneer', 'caseina']
_EGG = ['huevo', 'yema', 'omelet', 'revoltillo', 'mayonesa', 'merengue', 'pericos$']
_FISH = ['pescado', 'atun', 'salmon', 'trucha', 'tilapia', 'bagre', 'sardina', 'bacalao', 'mojarra',
         'pargo', 'corvina', 'merluza', 'anchoa', 'robalo', 'cazon']
_SHELLFISH = ['marisco', 'camaron', 'langostino', 'jaiba', 'cangrejo', 'langosta', 'calamar', 'pulpo',
              'almeja', 'ostra', 'mejillon', 'piangua']
_PORK = ['cerdo', 'chicharron', 'jamon', 'tocino', 'chorizo', 'morcilla', 'lechona', 'marrano', 'pernil',
         'panceta', 'salchicha']
_MEAT = ['carne', 'res$', 'ternera', 'pollo', 'pavo', 'cordero', 'higado', 'chunchullo', 'costilla',
         'bistec', 'sobrebarriga', 'gallina', 'pato$', 'hamburguesa', 'lomo']

ALLERGEN_WORDS = {
    'gluten': ['trigo', 'harina', 'pan$', 'panes$', 'pasta$', 'pastas$', 'espagueti', 'macarron', 'fideo',
               'avena', 'cebada', 'centeno', 'galleta', 'tostada', 'cuscus', 'seitan', 'cereal',
               'mogolla', 'empanada', 'bunuelo', 'pizza', 'cerveza'],
    'milk': _MILK,
    'egg': _EGG,
    'peanut': ['mani$', 'manies$', 'cacahuate', 'cacahuete'],
    'tree_nut': ['almendra', 'nuez', 'nueces$', 'avellana', 'pistacho', 'anacardo', 'maranon', 'castana',
                 'pecana', 'macadamia'],
    'fish': _FISH,
    'shellfish': _SHELLFISH,
    'soy': ['soya', 'soja', 'tofu', 'tempeh', 'edamame', 'miso$'],
    'sesame': ['ajonjoli', 'sesamo', 'tahini', 'tahina'],
}
DIET_WORDS = {
    'vegan': _words(_MEAT, _PORK, _FISH, _SHELLFISH, _MILK, _EGG, ['miel$', 'gelatina']),
    'vegetarian': _words(_MEAT, _PORK, _FISH, _SHELLFISH, ['gelatina']),
    'pescatarian': _words(_MEAT, _PORK),
    'halal': _words(_PORK),
    'kosher': _words(_PORK, _SHELLFISH),
}


def _compile(words):
    prefix = [w for w in words if not w.endswith('$')]
    exact = [w[:-1] for w in words if w.endswith('$')]
    parts = []
    if prefix:
        parts.append(r'\b(?:' + '|'.join(prefix) + r')\w*')
    if exact:
        parts.append(r'\b(?:' + '|'.join(exact) + r')\b')
    return re.compile('|'.join(parts))


_ALLERGEN_RE = {code: _compile(words) for code, words in ALLERGEN_WORDS.items()}
_DIET_RE = {code: _compile(words) for code, words in DIET_WORDS.items()}


def safe_phrases(foods):
    """Frases de alimentos PERMITIDOS que contienen palabras de la lista (p. ej. "mantequilla de
    maní" no es lácteo). Se borran del texto antes de buscar: lo que ya pasó el filtro por etiquetas
    es seguro por definición."""
    phrases = set()
    for food in foods:
        plain = normalize_for_search(food.name)
        phrases.add(plain)
        no_paren = re.sub(r'\s*\(.*?\)', '', plain).strip()
        phrases.add(no_paren)
        for cut in (' sin ', ' cocid', ' crud', ' tajad', ' escurrid'):
            if cut in no_paren:
                phrases.add(no_paren.split(cut)[0].strip())
    return sorted((p for p in phrases if len(p) >= 4), key=len, reverse=True)


def forbidden_in_text(text, blocked_allergens=(), diet='', safe=()):
    """Códigos (alérgenos y dieta) que el texto nombra y no debería. `text` puede traer tildes."""
    plain = normalize_for_search(text)
    for phrase in safe:
        plain = plain.replace(phrase, ' ')
    found = [code for code in blocked_allergens if code in _ALLERGEN_RE and _ALLERGEN_RE[code].search(plain)]
    if diet in _DIET_RE and _DIET_RE[diet].search(plain):
        found.append(f'diet:{diet}')
    return found


# ─── Reglas de la comida ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExistingOption:
    """Una opción ya activa de la comida (para que la nueva no repita a las demás)."""
    name: str
    food_ids: frozenset


@dataclass(frozen=True)
class SlotRules:
    slot_code: str
    kcal: Decimal
    protein_g: Decimal
    foods: dict                         # id -> FoodItem (solo los permitidos)
    blocked_allergens: tuple = ()
    diet: str = ''
    n_options: int = OPTIONS_PER_SLOT
    existing: tuple = ()                # ExistingOption


def _round5(value):
    return (D(value) / 5).quantize(D('1'), rounding=ROUND_HALF_UP) * 5


def scale_option(rows, target_kcal):
    """Ajusta las porciones para acercarse a la energía de la comida: escala entre 0,7 y 1,3 y
    redondeo a 5 g (mínimo 5, máximo 600). `rows` = [(FoodItem, gramos)]."""
    raw = f.items_totals(rows).kcal
    if raw <= 0:
        return list(rows)
    scale = min(max(D(target_kcal) / raw, SCALE_MIN), SCALE_MAX)
    return [(food, int(min(max(_round5(D(grams) * scale), MIN_GRAMS), MAX_GRAMS))) for food, grams in rows]


def kcal_in_band(kcal, target):
    return abs(kcal - target) <= target * KCAL_TOLERANCE


def evaluate_option(rows, rules):
    """Escala las porciones y comprueba energía (V04) y macros (V05). Devuelve
    `(filas escaladas, Totals, [códigos])`."""
    scaled = scale_option(rows, rules.kcal)
    total = f.items_totals(scaled)
    errors = []
    if not kcal_in_band(total.kcal, rules.kcal):
        errors.append((V04_KCAL, 'energía fuera de ±5 % tras ajustar las porciones'))
    else:
        if total.protein < rules.protein_g * PROTEIN_MIN_SHARE:
            errors.append((V05_MACROS, 'proteína por debajo del 90 % del objetivo de la comida'))
        if total.fat_share < FAT_MIN_SHARE:
            errors.append((V05_MACROS, 'grasa por debajo del 20 % de la energía'))
    return scaled, total, errors


def _jaccard(a, b):
    union = a | b
    return D(len(a & b)) / D(len(union)) if union else D('0')


def validate_meal_slot(data, rules):
    """Valida un `meal-slot/1`. Con todo bien devuelve `ValidationResult.data = {'options': [...]}`
    con textos limpios, porciones ajustadas y totales (`Totals`)."""
    errors = []
    if not _exact_keys(data, _TOP_KEYS, 'comida', errors):
        return ValidationResult(None, tuple(errors))
    if data['schema_version'] != MEAL_SCHEMA_VERSION:
        errors.append((V01_SCHEMA, 'schema_version distinto'))
    if data['slot_code'] != rules.slot_code:
        errors.append((V01_SCHEMA, 'slot_code distinto al pedido'))
    options = data['options']
    if not isinstance(options, list):
        errors.append((V01_SCHEMA, 'options: no es una lista'))
        return ValidationResult(None, tuple(errors))
    if len(options) != rules.n_options:
        errors.append((V01_SCHEMA, f'options: {len(options)} opciones, se pidieron {rules.n_options}'))

    cleaned = []
    safe = safe_phrases(rules.foods.values())
    for o_index, option in enumerate(options, start=1):
        where = f'opción {o_index}'
        if not _exact_keys(option, _OPTION_KEYS, where, errors):
            continue
        name = check_text(option['name'], MAX_NAME, f'{where}.name', errors, required=True)
        preparation = check_text(option['preparation'], MAX_PREPARATION, f'{where}.preparation', errors,
                                 required=True)
        banned = forbidden_in_text(f'{name or ""} {preparation or ""}', rules.blocked_allergens, rules.diet, safe)
        if banned:
            errors.append((V03_ALLERGEN, f'{where}: el texto nombra algo que la clienta no puede comer'))
        rows = _option_rows(option['items'], where, rules, errors)
        if rows is None:
            continue
        scaled, total, option_errors = evaluate_option(rows, rules)
        errors.extend((code, f'{where}: {detail}') for code, detail in option_errors)
        cleaned.append({
            'name': name or '', 'preparation': preparation or '',
            'items': [{'food_id': food.id, 'grams': grams} for food, grams in scaled], 'totals': total,
        })

    if not errors:
        _check_variety(cleaned, rules, errors)
    if errors:
        return ValidationResult(None, tuple(errors))
    return ValidationResult({'options': cleaned}, ())


def _option_rows(items, where, rules, errors):
    if not isinstance(items, list):
        errors.append((V01_SCHEMA, f'{where}.items: no es una lista'))
        return None
    if not MIN_ITEMS <= len(items) <= MAX_ITEMS:
        errors.append((V01_SCHEMA, f'{where}.items: {len(items)} alimentos (permitido {MIN_ITEMS} a {MAX_ITEMS})'))
    rows, seen = [], set()
    for i_index, item in enumerate(items, start=1):
        label = f'{where}.alimento {i_index}'
        if not _exact_keys(item, _ITEM_KEYS, label, errors):
            continue
        food_id, grams = item['food_id'], item['grams']
        if not _is_int(food_id) or not _is_int(grams):
            errors.append((V01_SCHEMA, f'{label}: food_id y grams deben ser enteros'))
            continue
        if food_id not in rules.foods:
            errors.append((V02_IDS, f'{label}: alimento fuera del catálogo permitido'))
            continue
        if food_id in seen:
            errors.append((V01_SCHEMA, f'{label}: alimento repetido en la misma opción'))
            continue
        if not MIN_GRAMS <= grams <= MAX_GRAMS:
            errors.append((V01_SCHEMA, f'{label}: gramos fuera de {MIN_GRAMS} a {MAX_GRAMS}'))
            continue
        seen.add(food_id)
        rows.append((rules.foods[food_id], grams))
    return rows if len(rows) == len(items) else None


def _similar(a, b):
    return a[0] == b[0] or _jaccard(a[1], b[1]) >= VARIETY_MAX_OVERLAP


def _check_variety(cleaned, rules, errors):
    entries = [(normalize_for_search(o['name']), frozenset(i['food_id'] for i in o['items'])) for o in cleaned]
    existing = [(normalize_for_search(e.name), e.food_ids) for e in rules.existing]
    for index, entry in enumerate(entries):
        if any(_similar(entry, other) for other in entries[index + 1:]):
            errors.append((V06_VARIETY, 'las opciones se parecen demasiado entre sí'))
            return
        if any(_similar(entry, other) for other in existing):
            errors.append((V06_VARIETY, 'la opción se parece a una que ya existe'))
            return


@dataclass(frozen=True)
class OptionEdit:
    """Resultado de revisar una opción escrita por el entrenador (sin ajuste automático de
    porciones: los gramos son los que él puso)."""
    errors: tuple = ()
    name: str = ''
    preparation: str = ''
    items: tuple = ()               # ({'food_id', 'grams'}, ...)
    totals: object = None           # Totals
    out_of_band: bool = False       # energía fuera de ±5 % de la comida (aviso, no bloquea)
    low_protein: bool = False       # proteína bajo el 90 % (aviso, no bloquea)

    @property
    def ok(self):
        return not self.errors


def validate_trainer_option(name, preparation, items, rules):
    """Revisa una opción editada por el entrenador. Bloquean los errores de forma, de catálogo
    (alimento fuera de los permitidos), de texto y de alérgenos; la energía y la proteína fuera de
    banda solo avisan (el entrenador es el profesional)."""
    errors = []
    clean_name = check_text(name, MAX_NAME, 'nombre', errors, required=True)
    clean_prep = check_text(preparation, MAX_PREPARATION, 'preparación', errors, required=True)
    if forbidden_in_text(f'{clean_name or ""} {clean_prep or ""}', rules.blocked_allergens, rules.diet,
                         safe_phrases(rules.foods.values())):
        errors.append((V03_ALLERGEN, 'el texto nombra algo que la clienta no puede comer'))
    rows = _option_rows(items, 'opción', rules, errors)
    if rows is None or errors:
        return OptionEdit(errors=tuple(errors))
    total = f.items_totals(rows)
    return OptionEdit(
        name=clean_name, preparation=clean_prep,
        items=tuple({'food_id': food.id, 'grams': grams} for food, grams in rows), totals=total,
        out_of_band=not kcal_in_band(total.kcal, rules.kcal),
        low_protein=total.protein < rules.protein_g * PROTEIN_MIN_SHARE,
    )


# ─── Banda del día (todas las combinaciones) ──────────────────────────────────

def check_day(slot_options, kcal_target, protein_target, kcal_floor):
    """`slot_options`: por comida, lista de `Totals` de sus opciones. Comprueba que TODA combinación
    (una opción por comida) quede en la banda de energía, con la proteína mínima y la grasa
    mínima. Como las comidas son independientes, basta mirar el mínimo y el máximo de cada una.
    Devuelve la lista de códigos con falla (vacía = bien)."""
    low_total = sum(min(t.kcal for t in options) for options in slot_options)
    high_total = sum(max(t.kcal for t in options) for options in slot_options)
    protein_min = sum(min(t.protein for t in options) for options in slot_options)
    fat_kcal_min = sum(min(t.fat * 9 for t in options) for options in slot_options)
    floor = min(D(kcal_floor), D(kcal_target))      # si el piso supera el objetivo, vale el objetivo
    slack = D('10')                                 # redondeos del reparto por comida
    low_limit = max(floor, D(kcal_target) * (1 - KCAL_TOLERANCE)) - slack
    high_limit = D(kcal_target) * (1 + KCAL_TOLERANCE) + slack
    codes = []
    if low_total < low_limit or high_total > high_limit:
        codes.append(V04_KCAL)
    if protein_min < D(protein_target) * PROTEIN_MIN_SHARE or (high_total and fat_kcal_min / high_total < FAT_MIN_SHARE):
        codes.append(V05_MACROS)
    return codes
