#!/usr/bin/env python
"""Mide cuántas recetas del recetario se pueden ofrecer de verdad a perfiles realistas.

Usa el mismo motor que la aplicación (energía por objetivo, reparto por comida y ajuste de porciones) con los
alimentos que hay en PRODUCCIÓN (los de referencia USDA revisada), sin tocar ninguna base de datos.

    python scripts/recipe_coverage.py            # tabla por comida y por perfil
    python scripts/recipe_coverage.py --min 6    # sale con error si algún caso queda bajo el umbral

El menú semanal necesita al menos 6 recetas distintas por comida (3 opciones x 2 días sin repetirse).
"""
import json
import os
import sys
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

# (nombre, edad, sexo, peso, estatura, actividad, objetivo)
CLIENTS = [
    ('F 28a 58kg, tonificación', 28, 'F', 58, '1.62', 'light', 'toning'),
    ('F 40a 82kg, pérdida de peso', 40, 'F', 82, '1.64', 'sedentary', 'weight_loss'),
    ('M 30a 78kg, ganancia muscular', 30, 'M', 78, '1.76', 'active', 'muscle_gain'),
    ('M 45a 95kg, pérdida de peso', 45, 'M', 95, '1.78', 'light', 'weight_loss'),
    ('F 35a 64kg, resistencia', 35, 'F', 64, '1.66', 'active', 'cardio'),
]
SLOTS = ('breakfast', 'lunch', 'dinner', 'mid_morning', 'snack')
# (nombre, dieta, alergias)
PROFILES = [
    ('omnívora', 'omnivore', ()),
    ('vegetariana', 'vegetarian', ()),
    ('vegana', 'vegan', ()),
    ('pescetariana', 'pescatarian', ()),
    ('sin lácteos', 'omnivore', ('milk',)),
    ('sin gluten', 'omnivore', ('gluten',)),
    ('sin huevo ni lácteos', 'omnivore', ('egg', 'milk')),
    ('vegana sin soya', 'vegan', ('soy',)),
    ('sin frutos secos ni maní', 'omnivore', ('peanut', 'tree_nut')),
    ('sin pescado ni mariscos', 'omnivore', ('fish', 'shellfish')),
]


def production_foods():
    """Alimentos que la aplicación puede usar en producción (referencia USDA revisada)."""
    from apps.plans.foods_data import DIETS_BY_CLASS, FOODS
    from apps.plans.rules.foods import FoodItem
    reference = json.loads((ROOT / 'apps' / 'plans' / 'data' / 'usda_reference.json').read_text(encoding='utf-8'))
    reviewed = {e['name'] for e in reference['foods'] if e['status'] == 'reviewed_reference'}
    items = {}
    for i, row in enumerate(FOODS, start=1):
        name, group, kcal, protein, carbs, fat, fiber, household, allergens, traces, cls, conds, common = row
        if name not in reviewed:
            continue
        items[i] = FoodItem(
            id=i, name=name, group=group, kcal100=D(str(kcal)), protein100=D(str(protein)), carbs100=D(str(carbs)),
            fat100=D(str(fat)), fiber100=D(str(fiber)), allergen_tags=tuple(allergens), trace_tags=tuple(traces),
            diet_tags=tuple(DIETS_BY_CLASS[cls]), condition_tags=tuple(conds), is_common=common)
    return items


def slot_targets(client):
    from apps.plans.rules import energy
    _name, age, sex, weight, height, activity, goal = client
    plan = energy.compute_energy(energy.EnergyInput(
        age=age, sex=sex, weight_kg=D(str(weight)), height_m=D(height), activity_level=activity, goal=goal))
    slots = energy.split_meals(plan, ('breakfast', 'mid_morning', 'lunch', 'snack', 'dinner'))
    return {s.code: s for s in slots}


def usable_recipes(foods, targets, diet, allergies, slot):
    from apps.plans.rules import meal_validate as meal
    from apps.plans.rules import recipes
    allowed = {
        i: f for i, f in foods.items()
        if diet in f.diet_tags and not any(a in f.allergen_tags or a in f.trace_tags for a in allergies)}
    target = targets[slot]
    rules = meal.SlotRules(slot_code=slot, kcal=target.kcal, protein_g=target.protein_g, foods=allowed)
    return {o['name'] for o in recipes.candidates(rules, 'basic')}


def main(minimum=None):
    import django
    django.setup()
    foods = production_foods()
    targets = [slot_targets(c) for c in CLIENTS]
    from apps.plans.rules import recipes
    print(f'Recetario {recipes.VERSION}: {len(recipes.RECIPES)} recetas | alimentos de producción: {len(foods)}')
    print('Recetas distintas ofrecibles (unión de los 5 perfiles de clienta) por comida y tipo de dieta/alergia\n')
    print(f'{"perfil":28s}' + ''.join(f'{s:>12s}' for s in SLOTS))
    worst = []
    for label, diet, allergies in PROFILES:
        row = []
        for slot in SLOTS:
            union = set()
            for t in targets:
                union |= usable_recipes(foods, t, diet, allergies, slot)
            row.append(len(union))
            if minimum is not None and len(union) < minimum:
                worst.append((label, slot, len(union)))
        print(f'{label:28s}' + ''.join(f'{n:12d}' for n in row))
    # Un perfil concreto: el caso que de verdad ve una clienta
    print('\nPor clienta (omnívora): recetas ofrecibles para su propia energía y proteína')
    print(f'{"clienta":34s}' + ''.join(f'{s:>12s}' for s in SLOTS))
    for c, t in zip(CLIENTS, targets):
        print(f'{c[0]:34s}' + ''.join(f'{len(usable_recipes(foods, t, "omnivore", (), s)):12d}' for s in SLOTS))
    if worst:
        print('\nBajo el umbral:', ', '.join(f'{p}/{s}={n}' for p, s, n in worst))
        return 1
    return 0


if __name__ == '__main__':
    threshold = int(sys.argv[sys.argv.index('--min') + 1]) if '--min' in sys.argv else None
    sys.exit(main(threshold))
