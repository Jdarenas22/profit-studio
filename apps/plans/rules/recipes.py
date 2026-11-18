"""Recetas de bienestar: combinaciones culinarias explícitas, no prescripciones.
Los nutrientes proceden exclusivamente del catálogo permitido recibido.
"""
import itertools
import random
import copy
from decimal import Decimal

from . import meal_validate as meal

VERSION = 'everyday-world/1'
B = ('breakfast',)
S = ('mid_morning', 'snack')
M = ('lunch', 'dinner')

# Nombre, cocina, tiempos, preparación y (ingrediente exacto del catálogo, mínimo/máximo g).
RECIPES = []


def add(name, cuisine, slots, preparation, *ingredients, cooking='basic'):
    RECIPES.append((name, cuisine, slots, preparation, ingredients, cooking))


add('Arepa con huevo y aguacate', 'colombiana', B,
    'Sirve la arepa caliente con el huevo cocido y el aguacate en láminas.',
    ('Arepa de maíz', 50, 130), ('Huevo entero cocido', 60, 180), ('Aguacate', 20, 70))
add('Tostadas con huevo y tomate', 'internacional', B,
    'Tuesta el pan y acompáñalo con el huevo cocido y el tomate. Usa el aceite como aderezo.',
    ('Pan integral tajado', 40, 100), ('Huevo entero cocido', 80, 180), ('Tomate', 30, 90), ('Aceite de oliva', 5, 15))
add('Avena con yogur griego, banano y maní', 'internacional', B + S,
    'Mezcla la avena con el yogur y deja que se hidrate en refrigeración. Agrega el banano y el maní al servir.',
    ('Avena en hojuelas (cruda)', 20, 70), ('Yogur griego natural', 100, 300),
    ('Banano', 40, 130), ('Maní tostado sin sal', 10, 30), cooking='none')
add('Pan con queso y papaya al lado', 'cotidiana', B,
    'Sirve el pan con el queso. Presenta la papaya cortada en un recipiente aparte.',
    ('Pan integral tajado', 40, 100), ('Queso fresco campesino', 35, 85), ('Papaya', 60, 180), cooking='none')
add('Bowl de yogur, manzana y almendras', 'internacional', B + S,
    'Sirve el yogur con la manzana picada y las almendras.',
    ('Yogur griego natural', 100, 320), ('Manzana', 50, 180), ('Almendras', 10, 35), cooking='none')
add('Arepa con queso y huevo', 'colombiana', B,
    'Calienta la arepa, añade el queso y sirve con el huevo cocido.',
    ('Arepa de maíz', 50, 120), ('Queso mozzarella semidescremado', 25, 65), ('Huevo entero cocido', 50, 130))
add('Bowl de soya, avena y mantequilla de maní', 'internacional', B + S,
    'Hidrata la avena con la bebida de soya en refrigeración. Incorpora la mantequilla de maní y el banano.',
    ('Bebida de soya sin azúcar', 140, 350), ('Avena en hojuelas (cruda)', 20, 80),
    ('Mantequilla de maní sin azúcar', 10, 35), ('Banano', 40, 130), cooking='none')
add('Yogur griego con mango y chía', 'internacional', S + B,
    'Mezcla la chía con el yogur, deja hidratar en refrigeración y añade el mango picado.',
    ('Yogur griego natural', 100, 300), ('Mango', 40, 160), ('Semillas de chía', 10, 30), cooking='none')
add('Cottage con fresas y nueces', 'internacional', S + B,
    'Sirve el queso cottage con las fresas cortadas y las nueces.',
    ('Queso cottage', 100, 250), ('Fresa', 50, 180), ('Nueces', 10, 30), cooking='none')
add('Sándwich frío de atún y aguacate', 'internacional', S,
    'Rellena el pan con el atún escurrido, el aguacate y el pepino.',
    ('Pan integral tajado', 30, 85), ('Atún en agua (escurrido)', 50, 120),
    ('Aguacate', 20, 60), ('Pepino cohombro', 20, 70), cooking='none')
add('Pan con hummus y huevo', 'mediterránea', S,
    'Unta el hummus sobre el pan y añade el huevo cocido en rodajas.',
    ('Pan integral tajado', 30, 80), ('Hummus', 30, 90), ('Huevo entero cocido', 50, 120))
add('Queso fresco con pera y maní', 'cotidiana', S,
    'Presenta el queso en cubos, la pera en gajos y el maní como acompañamientos separados.',
    ('Queso fresco campesino', 30, 75), ('Pera', 50, 150), ('Maní tostado sin sal', 10, 30), cooking='none')

add('Tortillas con huevo y tomate', 'mexicana inspirada', B + S,
    'Calienta las tortillas y sirve con huevo cocido y tomate. Añade el aceite como aderezo.',
    ('Tortilla de maíz', 40, 110), ('Huevo entero cocido', 70, 180), ('Tomate', 30, 90), ('Aceite de oliva', 5, 15))
add('Bowl de arroz, tofu y zanahoria', 'asiática inspirada', B + S,
    'Sirve el tofu y el arroz cocidos con zanahoria rallada y aceite como aderezo.',
    ('Arroz integral cocido', 50, 180), ('Tofu firme', 80, 220),
    ('Zanahoria cruda', 30, 90), ('Aceite de oliva', 5, 15))
add('Papa con huevo y espinaca', 'internacional', B + S,
    'Sirve la papa y la espinaca cocidas con el huevo. Añade el aceite al servir.',
    ('Papa pastusa cocida', 80, 220), ('Huevo entero cocido', 70, 180),
    ('Espinaca cocida', 30, 100), ('Aceite de oliva', 5, 15))
add('Tortillas de atún con pepino', 'mexicana inspirada', B + S,
    'Rellena las tortillas con atún escurrido, pepino y aguacate.',
    ('Tortilla de maíz', 40, 110), ('Atún en agua (escurrido)', 60, 160),
    ('Pepino cohombro', 30, 90), ('Aguacate', 30, 80), cooking='none')

for fruit in ('Papaya', 'Piña', 'Fresa', 'Banano', 'Mango', 'Melón', 'Guayaba'):
    add('Yogur griego con ' + fruit.lower(), 'internacional', S,
        'Sirve el yogur natural frío con la fruta cortada al lado.',
        ('Yogur griego natural', 90, 250), (fruit, 30, 140), cooking='none')

for protein, title in (
    ('Pechuga de pollo cocida sin piel', 'Pollo'),
    ('Tilapia cocida', 'Pescado'),
    ('Carne de res magra cocida', 'Res magra'),
    ('Tofu firme', 'Tofu'),
):
    add(title + ' con arroz y ensalada', 'cotidiana', M,
        'Sirve la proteína y el arroz calientes. Acompaña con tomate, lechuga y aceite como aderezo.',
        (protein, 70, 200), ('Arroz blanco cocido', 90, 250), ('Tomate', 40, 100),
        ('Lechuga', 30, 70), ('Aceite de oliva', 10, 25))
    add('Bowl de ' + title.lower() + ' con quinua', 'internacional', M,
        'Sirve la proteína cocida sobre la quinua y acompaña con pepino y aguacate.',
        (protein, 70, 200), ('Quinua cocida', 90, 230), ('Pepino cohombro', 40, 100), ('Aguacate', 40, 110))
    add(title + ' con papa y brócoli', 'internacional', M,
        'Sirve la proteína cocida con papa y brócoli. Añade el aceite como aderezo.',
        (protein, 70, 200), ('Papa pastusa cocida', 100, 280),
        ('Brócoli cocido', 50, 150), ('Aceite de oliva', 10, 25))
    add('Tacos de ' + title.lower() + ' con aguacate', 'mexicana inspirada', M,
        'Calienta las tortillas y rellénalas con la proteína cocida, tomate y aguacate.',
        (protein, 70, 180), ('Tortilla de maíz', 50, 130), ('Tomate', 40, 100), ('Aguacate', 40, 100))
    add(title + ' con yuca y calabacín', 'cotidiana', M,
        'Sirve la proteína cocida con yuca y calabacín. Añade el aceite al servir.',
        (protein, 70, 200), ('Yuca cocida', 90, 230),
        ('Calabacín cocido', 50, 150), ('Aceite vegetal (girasol o canola)', 10, 25))
    add(title + ' con plátano y zanahoria', 'cotidiana', M,
        'Sirve la proteína cocida con plátano asado y zanahoria rallada. Usa el aceite como aderezo.',
        (protein, 70, 200), ('Plátano maduro asado o cocido', 90, 240),
        ('Zanahoria cruda', 40, 120), ('Aceite vegetal (girasol o canola)', 10, 25))

add('Lentejas con arroz integral y aguacate', 'cotidiana', M,
    'Sirve las lentejas y el arroz calientes, con aguacate y tomate como acompañamientos.',
    ('Lentejas cocidas', 160, 350), ('Arroz integral cocido', 70, 180),
    ('Aguacate', 40, 100), ('Tomate', 40, 100))
add('Fríjoles con arroz y huevo', 'colombiana', M,
    'Sirve los fríjoles y el arroz calientes, con huevo cocido y aguacate al lado.',
    ('Fríjol rojo cocido', 120, 260), ('Arroz blanco cocido', 70, 180),
    ('Huevo entero cocido', 50, 120), ('Aguacate', 30, 90))
add('Pasta con atún y tomate', 'mediterránea inspirada', M,
    'Mezcla la pasta caliente con el atún escurrido, el tomate picado y el aceite.',
    ('Pasta cocida', 120, 280), ('Atún en agua (escurrido)', 70, 160),
    ('Tomate', 50, 130), ('Aceite de oliva', 10, 25))
add('Ensalada de garbanzos y huevo', 'mediterránea', M,
    'Mezcla los garbanzos cocidos con pepino y tomate. Acompaña con huevo y aceite como aderezo.',
    ('Garbanzos cocidos', 120, 280), ('Huevo entero cocido', 50, 130),
    ('Pepino cohombro', 40, 100), ('Tomate', 40, 100), ('Aceite de oliva', 5, 20))
add('Salmón con batata y espinaca', 'internacional', M,
    'Sirve el salmón cocido con la batata y la espinaca como acompañamientos.',
    ('Salmón cocido', 90, 220), ('Batata (camote) cocida', 150, 350), ('Espinaca cocida', 50, 150))


def candidates(rules, cooking='basic'):
    """Una porción viable por receta; sin sustituciones improvisadas cuando falta un ingrediente."""
    by_name = {food.name: food for food in rules.foods.values()}
    options = []
    for name, cuisine, slots, preparation, ingredients, effort in RECIPES:
        if rules.slot_code not in slots or (cooking == 'none' and effort != 'none'):
            continue
        if any(ingredient not in by_name for ingredient, _low, _high in ingredients):
            continue
        rng = random.Random(f'{VERSION}:{name}:{rules.kcal}:{rules.protein_g}')
        for _ in range(350):
            rows = [(by_name[ingredient], rng.randrange(low // 5, high // 5 + 1) * 5)
                    for ingredient, low, high in ingredients]
            scaled, _totals, errors = meal.evaluate_option(rows, rules)
            if errors:
                continue
            # Margen para el redondeo por comida y TODA combinación del día (no solo una).
            _checked_rows, checked, recheck_errors = meal.evaluate_option(scaled, rules)
            if recheck_errors or checked.protein < rules.protein_g or checked.fat_share < Decimal('.24'):
                continue
            if any(not Decimal(low) * Decimal('.7') <= grams <= Decimal(high) * Decimal('1.3')
                   for (_food, grams), (_name, low, high) in zip(scaled, ingredients)):
                continue
            options.append({
                'name': name, 'cuisine': cuisine, 'preparation': preparation,
                'items': [{'food_id': food.id, 'grams': grams} for food, grams in scaled],
            })
            break
    return options


def select_options(options, count, existing=(), avoid_names=()):
    """Busca un grupo completo con variedad; no deja la elección en un greedy incompleto."""
    avoid = {name.casefold() for name in avoid_names} | {option.name.casefold() for option in existing}
    entries = [(option, frozenset(item['food_id'] for item in option['items'])) for option in options
               if option['name'].casefold() not in avoid]
    entries = [(option, ids) for option, ids in entries
               if all(meal._jaccard(ids, other.food_ids) < meal.VARIETY_MAX_OVERLAP for other in existing)]
    for group in itertools.combinations(entries, count):
        if all(meal._jaccard(left[1], right[1]) < meal.VARIETY_MAX_OVERLAP
               for left, right in itertools.combinations(group, 2)):
            return [copy.deepcopy({key: value for key, value in option.items() if key != 'cuisine'})
                    for option, _ids in group]
    return []

