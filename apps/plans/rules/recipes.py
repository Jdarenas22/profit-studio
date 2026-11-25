"""Recetas de bienestar: combinaciones culinarias explícitas, no prescripciones.
Los nutrientes proceden exclusivamente del catálogo permitido recibido.

Variedad (everyday-world/2): el recetario se ofrece en un orden que cambia con cada trabajo (sin
sesgo hacia el primer plato del catálogo), no se repiten nombres dentro del mismo día cuando hay con
qué (`offer`) y las opciones de una comida prefieren distinta proteína principal (`select_options`).
La lista que se ofrece a la IA se recorta a `MAX_OFFERED` para no inflar el esquema de la respuesta.
"""
import copy
import random
from decimal import Decimal
from functools import lru_cache
from typing import NamedTuple

from . import meal_validate as meal

VERSION = 'everyday-world/3'
B = ('breakfast',)
S = ('mid_morning', 'snack')
M = ('lunch', 'dinner')

# Proteína principal: familia del alimento que más define el plato. Se usa para que las opciones de
# una misma comida no sean todas "de pollo". Las frutas, cereales, verduras y grasas no cuentan.
PROTEIN_FAMILY = {
    'Pechuga de pollo cocida sin piel': 'pollo', 'Muslo de pollo cocido sin piel': 'pollo',
    'Carne de res magra cocida': 'res', 'Carne molida de res magra cocida': 'res',
    'Lomo de cerdo magro cocido': 'cerdo', 'Pechuga de pavo cocida': 'pavo',
    'Tilapia cocida': 'tilapia', 'Salmón cocido': 'salmón', 'Atún en agua (escurrido)': 'atún',
    'Camarón cocido': 'camarón',
    'Huevo entero cocido': 'huevo', 'Clara de huevo cocida': 'huevo',
    'Fríjol rojo cocido': 'fríjol', 'Fríjol negro cocido': 'fríjol', 'Lentejas cocidas': 'lentejas',
    'Garbanzos cocidos': 'garbanzo', 'Hummus': 'garbanzo', 'Tofu firme': 'tofu',
    'Yogur griego natural': 'yogur', 'Yogur natural sin azúcar': 'yogur',
    'Queso fresco campesino': 'queso', 'Queso mozzarella semidescremado': 'queso', 'Queso cottage': 'queso',
    'Bebida de soya sin azúcar': 'bebida de soya', 'Mantequilla de maní sin azúcar': 'maní',
    'Leche entera': 'leche', 'Leche semidescremada': 'leche', 'Leche descremada': 'leche',
}

# Nombre, cocina, tiempos, preparación, ingredientes (alimento exacto del catálogo, mínimo/máximo g),
# esfuerzo ('basic' o 'none' = sin cocción) y proteína principal.
RECIPES = []
PROTEIN_BY_NAME = {}        # nombre en minúsculas -> proteína principal (para las opciones ya existentes)


def add(name, cuisine, slots, preparation, *ingredients, cooking='basic', main=None):
    """`main` = alimento que define la proteína principal; por omisión, el primer ingrediente proteico."""
    lead = main or next((food for food, _low, _high in ingredients if food in PROTEIN_FAMILY), None)
    family = PROTEIN_FAMILY.get(lead)
    RECIPES.append((name, cuisine, slots, preparation, ingredients, cooking, family))
    PROTEIN_BY_NAME[name.casefold()] = family


# ─── Desayuno, media mañana y merienda ────────────────────────────────────────

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
    ('Pan integral tajado', 30, 80), ('Hummus', 30, 90), ('Huevo entero cocido', 50, 120),
    main='Huevo entero cocido')
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

# Nuevas en everyday-world/2
add('Calentado de arroz, fríjol y huevo', 'colombiana', B,
    'Calienta el arroz con los fríjoles, sirve con el huevo cocido y acompaña con la arepa.',
    ('Arroz blanco cocido', 50, 150), ('Fríjol rojo cocido', 60, 160),
    ('Huevo entero cocido', 60, 150), ('Arepa de maíz', 30, 90))
add('Arepa con pavo, queso y tomate', 'colombiana', B,
    'Calienta la arepa y rellénala con el pavo, el queso y el tomate en rodajas.',
    ('Arepa de maíz', 50, 120), ('Pechuga de pavo cocida', 40, 110),
    ('Queso mozzarella semidescremado', 20, 60), ('Tomate', 30, 90))
add('Tortillas con fríjol negro, huevo y aguacate', 'mexicana inspirada', B,
    'Calienta las tortillas y sírvelas con el fríjol negro, el huevo cocido picado y el aguacate.',
    ('Tortilla de maíz', 40, 110), ('Fríjol negro cocido', 50, 150),
    ('Huevo entero cocido', 50, 130), ('Aguacate', 20, 60))
add('Arepa con huevo, clara y tomate', 'colombiana', B,
    'Sirve la arepa caliente con el huevo y la clara cocidos y el tomate. Usa el aceite como aderezo.',
    ('Arepa de maíz', 50, 120), ('Huevo entero cocido', 40, 110), ('Clara de huevo cocida', 40, 150),
    ('Tomate', 30, 90), ('Aceite de oliva', 5, 15))
add('Tostadas con queso fresco, clara y aguacate', 'cotidiana', B,
    'Tuesta el pan y acompáñalo con el queso en láminas, la clara cocida y el aguacate.',
    ('Pan blanco tajado', 40, 100), ('Queso fresco campesino', 30, 80),
    ('Clara de huevo cocida', 40, 160), ('Aguacate', 20, 70))
add('Batido de leche, banano, avena y mantequilla de maní', 'internacional', B + S,
    'Licúa la leche con el banano, la avena y la mantequilla de maní hasta que quede suave.',
    ('Leche semidescremada', 200, 400), ('Banano', 50, 130), ('Avena en hojuelas (cruda)', 20, 60),
    ('Mantequilla de maní sin azúcar', 10, 30), cooking='none')
add('Avena con leche, mora y linaza', 'internacional', B + S,
    'Mezcla la avena con la leche y deja que se hidrate en refrigeración. Añade la mora y la linaza al servir.',
    ('Leche entera', 150, 350), ('Avena en hojuelas (cruda)', 25, 80), ('Mora', 40, 140),
    ('Linaza molida', 5, 20), cooking='none')
add('Yogur natural con avena, naranja y nueces', 'internacional', B + S,
    'Mezcla el yogur con la avena y refrigera un rato. Sirve con la naranja en gajos y las nueces.',
    ('Yogur natural sin azúcar', 120, 300), ('Avena en hojuelas (cruda)', 20, 60),
    ('Naranja', 60, 180), ('Nueces', 10, 30), cooking='none')
add('Sándwich de mantequilla de maní y banano con leche', 'cotidiana', B,
    'Unta el pan con la mantequilla de maní, agrega el banano en rodajas y acompaña con la leche.',
    ('Pan integral tajado', 40, 100), ('Mantequilla de maní sin azúcar', 15, 40),
    ('Banano', 50, 130), ('Leche semidescremada', 150, 300), cooking='none',
    main='Leche semidescremada')
add('Tortillas con tofu, tomate y pimentón', 'mexicana inspirada', B,
    'Calienta las tortillas y rellénalas con el tofu desmenuzado, el tomate, el pimentón y el aguacate.',
    ('Tortilla de maíz', 40, 110), ('Tofu firme', 80, 220), ('Tomate', 30, 90),
    ('Pimentón', 30, 80), ('Aguacate', 20, 60))
add('Tortillas con fríjol negro, tofu y aguacate', 'mexicana inspirada', B,
    'Calienta las tortillas y rellénalas con el fríjol negro, el tofu desmenuzado, el tomate y el aguacate.',
    ('Tortilla de maíz', 40, 100), ('Fríjol negro cocido', 40, 120), ('Tofu firme', 60, 160),
    ('Tomate', 30, 90), ('Aguacate', 20, 60), main='Fríjol negro cocido')
add('Papa criolla con tofu y espinaca', 'internacional', B + S,
    'Sirve la papa criolla y la espinaca cocidas con el tofu en cubos. Añade el aceite al servir.',
    ('Papa criolla cocida', 80, 220), ('Tofu firme', 80, 220), ('Espinaca cocida', 30, 100),
    ('Aceite de oliva', 5, 15))

add('Yogur natural con kiwi y linaza', 'internacional', S,
    'Sirve el yogur frío con el kiwi en rodajas y la linaza por encima.',
    ('Yogur natural sin azúcar', 100, 250), ('Kiwi', 50, 150), ('Linaza molida', 5, 20), cooking='none')
add('Yogur natural con mora y nueces', 'internacional', S,
    'Sirve el yogur frío con la mora y las nueces picadas.',
    ('Yogur natural sin azúcar', 120, 280), ('Mora', 40, 120), ('Nueces', 10, 25), cooking='none')
add('Galletas de soda con queso fresco y mandarina', 'cotidiana', S,
    'Sirve las galletas con el queso en láminas y la mandarina en gajos.',
    ('Galletas de soda', 15, 45), ('Queso fresco campesino', 30, 80), ('Mandarina', 60, 180), cooking='none')
add('Galletas de soda con atún y aguacate', 'cotidiana', S,
    'Unta el aguacate sobre las galletas y cubre con el atún escurrido.',
    ('Galletas de soda', 15, 45), ('Atún en agua (escurrido)', 40, 110), ('Aguacate', 20, 60), cooking='none')
add('Queso cottage con sandía y almendras', 'internacional', S,
    'Sirve el queso cottage con la sandía en cubos y las almendras.',
    ('Queso cottage', 100, 250), ('Sandía', 80, 250), ('Almendras', 10, 30), cooking='none')
add('Claras con aguacate y galletas de soda', 'cotidiana', S,
    'Pica las claras cocidas, sírvelas con el aguacate y acompaña con las galletas.',
    ('Clara de huevo cocida', 60, 200), ('Aguacate', 20, 70), ('Galletas de soda', 10, 40))
add('Leche descremada con avena, uvas y nueces', 'internacional', S + B,
    'Hidrata la avena con la leche en refrigeración y sirve con las uvas y las nueces.',
    ('Leche descremada', 150, 350), ('Avena en hojuelas (cruda)', 15, 50), ('Uvas', 40, 120),
    ('Nueces', 10, 25), cooking='none')
add('Rollitos de pavo con queso y pepino', 'cotidiana', S,
    'Enrolla las lonchas de pavo con el queso, el pepino en tiras y el aguacate.',
    ('Pechuga de pavo cocida', 40, 110), ('Queso mozzarella semidescremado', 15, 50),
    ('Pepino cohombro', 30, 90), ('Aguacate', 15, 50), cooking='none')
add('Pan con hummus y pepino', 'mediterránea', S,
    'Unta el hummus sobre el pan y acompaña con el pepino en rodajas.',
    ('Pan integral tajado', 30, 90), ('Hummus', 40, 130), ('Pepino cohombro', 30, 100), cooking='none')
add('Tortillas con hummus y zanahoria', 'mediterránea', S,
    'Calienta las tortillas, úntalas con el hummus y acompaña con la zanahoria en bastones.',
    ('Tortilla de maíz', 30, 90), ('Hummus', 40, 130), ('Zanahoria cruda', 30, 100), cooking='none')
add('Bebida de soya con avena, mora y chía', 'internacional', S + B,
    'Hidrata la avena y la chía con la bebida de soya en refrigeración. Sirve con la mora.',
    ('Bebida de soya sin azúcar', 150, 350), ('Avena en hojuelas (cruda)', 20, 60), ('Mora', 40, 120),
    ('Semillas de chía', 10, 25), cooking='none')
add('Batido de soya, banano y mantequilla de maní', 'internacional', S + B,
    'Licúa la bebida de soya con el banano y la mantequilla de maní.',
    ('Bebida de soya sin azúcar', 200, 400), ('Banano', 50, 130),
    ('Mantequilla de maní sin azúcar', 10, 30), cooking='none')
add('Pan con mantequilla de maní y manzana', 'cotidiana', S,
    'Unta el pan con la mantequilla de maní y acompaña con la manzana en gajos.',
    ('Pan integral tajado', 30, 80), ('Mantequilla de maní sin azúcar', 10, 30), ('Manzana', 50, 150),
    cooking='none')
add('Queso fresco con uvas y nueces', 'cotidiana', S,
    'Presenta el queso en cubos con las uvas y las nueces como acompañamiento.',
    ('Queso fresco campesino', 30, 75), ('Uvas', 50, 150), ('Nueces', 10, 25), cooking='none')

# ─── Almuerzo y cena ──────────────────────────────────────────────────────────

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

# Nuevas en everyday-world/2: otras proteínas y acompañantes
add('Arroz con camarones y pimentón', 'caribeña', M,
    'Saltea el pimentón y la cebolla en el aceite, agrega los camarones cocidos y mezcla con el arroz caliente.',
    ('Camarón cocido', 70, 200), ('Arroz blanco cocido', 90, 250), ('Pimentón', 30, 90),
    ('Cebolla cabezona', 20, 70), ('Aceite de oliva', 10, 30))
add('Ensalada de camarón, aguacate y papa criolla', 'latina', M,
    'Mezcla los camarones cocidos con la papa criolla, el aguacate y el tomate. Usa el aceite como aderezo.',
    ('Camarón cocido', 70, 200), ('Papa criolla cocida', 100, 300), ('Aguacate', 40, 110),
    ('Tomate', 30, 90), ('Aceite de oliva', 5, 15))
add('Lomo de cerdo con arracacha y habichuela', 'colombiana', M,
    'Sirve el cerdo cocido con la arracacha y la habichuela. Añade el aceite al servir.',
    ('Lomo de cerdo magro cocido', 70, 200), ('Arracacha cocida', 100, 260),
    ('Habichuela cocida', 50, 150), ('Aceite vegetal (girasol o canola)', 5, 20))
add('Cerdo con plátano verde y ensalada de repollo', 'colombiana', M,
    'Sirve el cerdo cocido con el plátano verde y una ensalada de repollo y zanahoria con el aceite.',
    ('Lomo de cerdo magro cocido', 70, 200), ('Plátano verde cocido', 90, 230),
    ('Repollo crudo', 40, 120), ('Zanahoria cruda', 20, 80), ('Aceite vegetal (girasol o canola)', 5, 20))
add('Cerdo salteado con arroz y pimentón', 'asiática inspirada', M,
    'Saltea el cerdo cocido con el pimentón y la zanahoria en el aceite y sirve sobre el arroz caliente.',
    ('Lomo de cerdo magro cocido', 70, 200), ('Arroz blanco cocido', 90, 250), ('Pimentón', 30, 90),
    ('Zanahoria cruda', 30, 90), ('Aceite vegetal (girasol o canola)', 5, 20))
add('Pavo con ahuyama y arveja', 'cotidiana', M,
    'Sirve el pavo con el arroz, la ahuyama y la arveja calientes. Añade el aceite al servir.',
    ('Pechuga de pavo cocida', 70, 200), ('Arroz integral cocido', 50, 150), ('Ahuyama cocida', 80, 220),
    ('Arveja verde cocida', 50, 150), ('Aceite de oliva', 10, 25))
add('Pavo con papa criolla y brócoli', 'internacional', M,
    'Sirve el pavo cocido con la papa criolla y el brócoli. Añade el aguacate y el aceite al servir.',
    ('Pechuga de pavo cocida', 70, 200), ('Papa criolla cocida', 100, 300), ('Brócoli cocido', 50, 150),
    ('Aguacate', 30, 80), ('Aceite de oliva', 5, 15))
add('Sándwich de pavo, queso y aguacate', 'internacional', B + M,
    'Arma el sándwich con el pavo, el queso, el aguacate y el tomate en rodajas.',
    ('Pan integral tajado', 50, 130), ('Pechuga de pavo cocida', 50, 140),
    ('Queso mozzarella semidescremado', 20, 60), ('Aguacate', 30, 80), ('Tomate', 30, 80), cooking='none')
add('Muslo de pollo con arroz y habichuela', 'cotidiana', M,
    'Sirve el muslo cocido con el arroz y la habichuela calientes. Añade el aceite al servir.',
    ('Muslo de pollo cocido sin piel', 80, 240), ('Arroz blanco cocido', 90, 250),
    ('Habichuela cocida', 50, 150), ('Aceite vegetal (girasol o canola)', 5, 15))
add('Muslo de pollo con arracacha y ensalada de repollo', 'colombiana', M,
    'Sirve el muslo cocido con la arracacha y una ensalada de repollo y zanahoria con el aceite.',
    ('Muslo de pollo cocido sin piel', 80, 240), ('Arracacha cocida', 100, 260), ('Repollo crudo', 40, 120),
    ('Zanahoria cruda', 20, 80), ('Aceite vegetal (girasol o canola)', 5, 15))
add('Muslo de pollo con ñame y ahuyama', 'caribeña', M,
    'Sirve el muslo cocido con el ñame y la ahuyama. Añade el aceite al servir.',
    ('Muslo de pollo cocido sin piel', 80, 240), ('Ñame cocido', 90, 240), ('Ahuyama cocida', 60, 180),
    ('Aceite de oliva', 5, 15))
add('Pollo con choclo, pimentón y aguacate', 'colombiana', M,
    'Sirve el pollo cocido con el choclo y el pimentón, y acompaña con el aguacate.',
    ('Pechuga de pollo cocida sin piel', 70, 200), ('Choclo (maíz tierno) cocido', 80, 220),
    ('Pimentón', 30, 90), ('Aguacate', 30, 90))
add('Arroz con carne molida y fríjol negro', 'latina', M,
    'Sirve el arroz caliente con la carne molida cocida y el fríjol negro, y acompaña con el aguacate.',
    ('Carne molida de res magra cocida', 60, 160), ('Arroz blanco cocido', 70, 180),
    ('Fríjol negro cocido', 60, 160), ('Aguacate', 30, 80))
add('Tacos de carne molida con repollo y tomate', 'mexicana inspirada', M,
    'Calienta las tortillas y rellénalas con la carne molida cocida, el repollo, el tomate y el aguacate.',
    ('Carne molida de res magra cocida', 70, 180), ('Tortilla de maíz', 50, 130), ('Repollo crudo', 30, 90),
    ('Tomate', 40, 100), ('Aguacate', 30, 80))
add('Pasta con carne molida y tomate', 'italiana inspirada', M,
    'Mezcla la pasta caliente con la carne molida cocida, el tomate y la cebolla salteados en el aceite.',
    ('Pasta cocida', 120, 280), ('Carne molida de res magra cocida', 70, 170), ('Tomate', 60, 160),
    ('Cebolla cabezona', 20, 70), ('Aceite de oliva', 5, 20))
add('Papa criolla con huevo y champiñones', 'internacional', M,
    'Saltea los champiñones en el aceite y sirve con la papa criolla cocida y el huevo cocido en cuartos.',
    ('Papa criolla cocida', 100, 300), ('Huevo entero cocido', 100, 250), ('Champiñones', 50, 150),
    ('Aceite de oliva', 5, 20))
add('Ensalada de papa, huevo y clara con aguacate', 'internacional', M,
    'Mezcla la papa cocida con el huevo y la clara picados, el pepino y el aguacate.',
    ('Papa pastusa cocida', 100, 280), ('Huevo entero cocido', 60, 150), ('Clara de huevo cocida', 60, 200),
    ('Aguacate', 30, 90), ('Pepino cohombro', 30, 90))
add('Lentejas con plátano maduro y aguacate', 'colombiana', M,
    'Sirve las lentejas calientes con el plátano asado, el aguacate y el tomate. Usa el aceite como aderezo.',
    ('Lentejas cocidas', 150, 330), ('Plátano maduro asado o cocido', 60, 160), ('Aguacate', 30, 90),
    ('Tomate', 30, 90), ('Aceite de oliva', 5, 15))
add('Arroz con fríjol negro y plátano maduro', 'caribeña', M,
    'Sirve el arroz y el fríjol negro calientes con el plátano asado y el aguacate. Añade el aceite al servir.',
    ('Fríjol negro cocido', 150, 300), ('Arroz blanco cocido', 50, 130),
    ('Plátano maduro asado o cocido', 40, 100), ('Aguacate', 40, 110), ('Aceite de oliva', 5, 15))
add('Estofado de garbanzos con espinaca y papa', 'mediterránea', M,
    'Calienta los garbanzos con la papa y la espinaca cocidas y termina con el aceite.',
    ('Garbanzos cocidos', 120, 300), ('Espinaca cocida', 50, 160), ('Papa pastusa cocida', 60, 180),
    ('Aceite de oliva', 10, 25))
add('Tofu salteado con champiñones y arroz integral', 'asiática inspirada', M,
    'Saltea el tofu con los champiñones y el pimentón en el aceite y sirve sobre el arroz caliente.',
    ('Tofu firme', 80, 220), ('Champiñones', 50, 150), ('Pimentón', 30, 100),
    ('Arroz integral cocido', 70, 200), ('Aceite vegetal (girasol o canola)', 5, 20))
add('Tacos de tofu con repollo y pimentón', 'mexicana inspirada', M,
    'Calienta las tortillas y rellénalas con el tofu, el repollo, el pimentón y el aguacate.',
    ('Tofu firme', 80, 200), ('Tortilla de maíz', 50, 130), ('Repollo crudo', 30, 90),
    ('Pimentón', 30, 80), ('Aguacate', 30, 80))
add('Tacos de fríjol negro y queso con aguacate', 'mexicana inspirada', M,
    'Calienta las tortillas y rellénalas con el fríjol negro, el queso, el repollo y el aguacate.',
    ('Fríjol negro cocido', 80, 200), ('Tortilla de maíz', 50, 130), ('Queso fresco campesino', 30, 80),
    ('Repollo crudo', 20, 70), ('Aguacate', 30, 80))
add('Bowl de quinua, huevo y espinaca', 'internacional', M,
    'Sirve la quinua con el huevo cocido en cuartos, la espinaca cocida, el queso y el aguacate.',
    ('Quinua cocida', 90, 230), ('Huevo entero cocido', 60, 160), ('Espinaca cocida', 40, 120),
    ('Queso fresco campesino', 20, 60), ('Aguacate', 30, 90), main='Huevo entero cocido')
add('Pasta con queso cottage y espinaca', 'mediterránea inspirada', M,
    'Mezcla la pasta caliente con el queso cottage, la espinaca cocida y el tomate. Añade el aceite al servir.',
    ('Pasta cocida', 120, 280), ('Queso cottage', 80, 220), ('Espinaca cocida', 40, 120),
    ('Tomate', 40, 100), ('Aceite de oliva', 5, 20))
add('Salmón con arroz integral y coliflor', 'internacional', M,
    'Sirve el salmón cocido con el arroz y la coliflor calientes.',
    ('Salmón cocido', 90, 220), ('Arroz integral cocido', 70, 200), ('Coliflor cocida', 60, 200))
add('Tilapia con arracacha y ensalada de remolacha', 'colombiana', M,
    'Sirve la tilapia cocida con la arracacha y una ensalada de remolacha y lechuga con el aceite.',
    ('Tilapia cocida', 90, 230), ('Arracacha cocida', 100, 260), ('Remolacha cocida', 40, 120),
    ('Lechuga', 20, 60), ('Aceite de oliva', 10, 25))
add('Ensalada de atún, papa, huevo y habichuela', 'mediterránea', M,
    'Mezcla el atún escurrido con la papa, el huevo y la habichuela cocidos. Usa el aceite como aderezo.',
    ('Atún en agua (escurrido)', 60, 150), ('Papa pastusa cocida', 80, 220), ('Huevo entero cocido', 40, 120),
    ('Habichuela cocida', 40, 120), ('Aceite de oliva', 5, 20))
add('Ensalada de lentejas, remolacha y huevo', 'mediterránea', M,
    'Mezcla las lentejas con la remolacha, agrega el huevo cocido en cuartos y usa el aceite como aderezo.',
    ('Lentejas cocidas', 120, 280), ('Remolacha cocida', 40, 120), ('Huevo entero cocido', 50, 130),
    ('Aceite de oliva', 5, 20))

# Sin cocción para quien no cocina (se presupone que lo cocido se consigue listo)
add('Ensalada de atún, fríjol negro y aguacate', 'latina', M,
    'Mezcla el atún escurrido con el fríjol negro, el aguacate y el tomate. Usa el aceite como aderezo.',
    ('Atún en agua (escurrido)', 60, 150), ('Fríjol negro cocido', 100, 220), ('Aguacate', 30, 90),
    ('Tomate', 40, 100), ('Aceite de oliva', 5, 15), cooking='none')
add('Bowl frío de garbanzos, queso cottage y pepino', 'mediterránea', M,
    'Mezcla los garbanzos con el queso cottage, el pepino y el tomate. Usa el aceite como aderezo.',
    ('Garbanzos cocidos', 100, 250), ('Queso cottage', 80, 220), ('Pepino cohombro', 40, 100),
    ('Tomate', 30, 90), ('Aceite de oliva', 5, 20), cooking='none', main='Queso cottage')
add('Bowl frío de quinua, atún y aguacate', 'internacional', M,
    'Sirve la quinua fría con el atún escurrido, el aguacate, el pepino y el tomate.',
    ('Atún en agua (escurrido)', 60, 150), ('Quinua cocida', 90, 230), ('Aguacate', 30, 90),
    ('Pepino cohombro', 30, 90), ('Tomate', 30, 90), cooking='none')
add('Rollos de tortilla con pavo y aguacate', 'mexicana inspirada', B + M,
    'Extiende las tortillas, reparte el pavo, el aguacate, la lechuga y el tomate, y enróllalas.',
    ('Tortilla de maíz', 50, 130), ('Pechuga de pavo cocida', 50, 150), ('Aguacate', 30, 90),
    ('Lechuga', 20, 60), ('Tomate', 30, 90), cooking='none')
add('Ensalada de lentejas, quinua y aguacate', 'internacional', M,
    'Mezcla las lentejas con la quinua, el tomate y el aguacate. Usa el aceite como aderezo.',
    ('Lentejas cocidas', 120, 280), ('Quinua cocida', 60, 180), ('Tomate', 40, 100),
    ('Aguacate', 30, 90), ('Aceite de oliva', 5, 15), cooking='none')
add('Ensalada fría de pasta con atún y pepino', 'mediterránea inspirada', M,
    'Mezcla la pasta fría con el atún escurrido, el pepino y el tomate. Usa el aceite como aderezo.',
    ('Pasta cocida', 100, 250), ('Atún en agua (escurrido)', 70, 160), ('Pepino cohombro', 30, 90),
    ('Tomate', 30, 90), ('Aceite de oliva', 5, 20), cooking='none')
add('Sándwich de atún, queso y tomate', 'internacional', M,
    'Arma el sándwich con el atún escurrido, el queso, el tomate en rodajas y el aguacate.',
    ('Pan integral tajado', 50, 130), ('Atún en agua (escurrido)', 60, 150),
    ('Queso mozzarella semidescremado', 20, 60), ('Tomate', 30, 90), ('Aguacate', 20, 70), cooking='none')
add('Ensalada de pavo, quinua y aguacate', 'internacional', M,
    'Mezcla la quinua con el pavo en tiras, el aguacate, el pepino y el tomate.',
    ('Pechuga de pavo cocida', 60, 160), ('Quinua cocida', 80, 200), ('Aguacate', 30, 90),
    ('Pepino cohombro', 30, 90), ('Tomate', 30, 90), cooking='none')
add('Bowl frío de lentejas, queso fresco y aguacate', 'cotidiana', M,
    'Sirve las lentejas frías con el queso en cubos, el aguacate y el tomate. Usa el aceite como aderezo.',
    ('Lentejas cocidas', 120, 280), ('Queso fresco campesino', 30, 90), ('Aguacate', 30, 90),
    ('Tomate', 30, 90), ('Aceite de oliva', 5, 15), cooking='none')
add('Bowl de hummus, garbanzos y quinua', 'mediterránea', M,
    'Sirve la quinua con los garbanzos y el hummus, y acompaña con pepino y zanahoria rallada.',
    ('Hummus', 40, 120), ('Garbanzos cocidos', 100, 250), ('Quinua cocida', 60, 170),
    ('Pepino cohombro', 40, 100), ('Zanahoria cruda', 20, 80), cooking='none')


# ─── Selección ────────────────────────────────────────────────────────────────

_STRIPPED = ('cuisine', 'main_protein')      # solo para elegir; no viajan en la opción devuelta
# Tope de recetas que se ofrecen a la IA en una llamada. Los nombres van en el enum del esquema y el
# proveedor rechaza esquemas demasiado grandes ("trim large enum lists", docs de salida estructurada
# de Gemini, consultado el 2026-11-19): antes de everyday-world/2 el máximo era 29. Ajustable.
# Extra combinations for weekly menus. Ingredient names resolve against the allowed catalogue;
# unavailable or incompatible foods exclude the whole recipe, without silent substitutions.
add('Arepa con tofu y aguacate', 'colombiana', B + S,
    'Calienta la arepa y sírvela con tofu cocido y aguacate en láminas.',
    ('Arepa de maíz', 40, 130), ('Tofu firme', 70, 220), ('Aguacate', 20, 80))
add('Tortillas con lentejas y tomate', 'mexicana inspirada', B + S,
    'Rellena las tortillas calientes con lentejas cocidas, tomate y aguacate.',
    ('Tortilla de maíz', 30, 110), ('Lentejas cocidas', 70, 200), ('Tomate', 30, 90), ('Aguacate', 20, 70))
add('Quinua con tofu, pepino y semillas', 'internacional', B + S,
    'Sirve la quinua cocida con tofu, pepino picado y semillas de chía. Usa el aceite como aderezo.',
    ('Quinua cocida', 50, 170), ('Tofu firme', 70, 200), ('Pepino cohombro', 30, 90),
    ('Semillas de chía', 5, 15), ('Aceite de oliva', 5, 15), cooking='none')
add('Batata con tofu y brócoli', 'internacional', B + M,
    'Sirve la batata, el tofu y el brócoli cocidos; añade aceite de oliva al servir.',
    ('Batata (camote) cocida', 70, 240), ('Tofu firme', 80, 230), ('Brócoli cocido', 40, 130), ('Aceite de oliva', 5, 20))
add('Papa con garbanzos y aguacate', 'cotidiana', B + S,
    'Mezcla la papa cocida con garbanzos, tomate y aguacate en cubos.',
    ('Papa pastusa cocida', 60, 190), ('Garbanzos cocidos', 70, 200), ('Tomate', 30, 90), ('Aguacate', 20, 70))
add('Arroz con lentejas, zanahoria y maní', 'internacional', B + M,
    'Sirve el arroz y las lentejas cocidos con zanahoria rallada y maní tostado sin sal.',
    ('Arroz blanco cocido', 50, 180), ('Lentejas cocidas', 70, 230),
    ('Zanahoria cruda', 30, 90), ('Maní tostado sin sal', 10, 30))
add('Tortillas con pollo, lechuga y aguacate', 'mexicana inspirada', B + M,
    'Rellena las tortillas con pollo cocido en tiras, lechuga y aguacate.',
    ('Tortilla de maíz', 40, 130), ('Pechuga de pollo cocida sin piel', 50, 180),
    ('Lechuga', 20, 70), ('Aguacate', 20, 80))
add('Tostadas con cottage, pera y nueces', 'internacional', B + S,
    'Sirve el pan tostado con cottage; acompaña con pera en gajos y nueces.',
    ('Pan integral tajado', 30, 100), ('Queso cottage', 70, 200), ('Pera', 40, 150), ('Nueces', 10, 25))
add('Bowl de soya, papaya y chía', 'internacional', B + S,
    'Hidrata la avena y la chía en la bebida de soya en refrigeración y sirve con papaya cortada.',
    ('Bebida de soya sin azúcar', 130, 350), ('Avena en hojuelas (cruda)', 20, 65),
    ('Papaya', 40, 140), ('Semillas de chía', 10, 25), cooking='none')
add('Batido de soya, fresa y almendras', 'internacional', B + S,
    'Licúa la bebida de soya con las fresas, avena y almendras. Sirve sin añadir azúcar.',
    ('Bebida de soya sin azúcar', 150, 350), ('Fresa', 50, 160),
    ('Avena en hojuelas (cruda)', 20, 60), ('Almendras', 10, 30), cooking='none')
add('Ensalada de garbanzos con pepino y maní', 'mediterránea inspirada', S + M,
    'Mezcla los garbanzos cocidos con pepino, tomate y maní; añade aceite como aderezo.',
    ('Garbanzos cocidos', 70, 240), ('Pepino cohombro', 30, 100), ('Tomate', 30, 100),
    ('Maní tostado sin sal', 10, 25), ('Aceite de oliva', 5, 15), cooking='none')
add('Ensalada de tofu, mango y quinua', 'internacional', S + M,
    'Mezcla la quinua y el tofu cocidos y fríos con mango y pepino; añade aceite como aderezo.',
    ('Tofu firme', 70, 230), ('Quinua cocida', 50, 200), ('Mango', 40, 130),
    ('Pepino cohombro', 30, 90), ('Aceite de oliva', 5, 20), cooking='none')
add('Pollo con pasta y brócoli', 'mediterránea inspirada', M,
    'Mezcla la pasta y el brócoli cocidos con pollo en tiras. Añade aceite de oliva al servir.',
    ('Pechuga de pollo cocida sin piel', 70, 210), ('Pasta cocida', 90, 260),
    ('Brócoli cocido', 60, 160), ('Aceite de oliva', 10, 25))
add('Pavo con papa criolla y habichuela', 'cotidiana', M,
    'Sirve el pavo cocido con papa criolla y habichuela cocidas; acompaña con aguacate.',
    ('Pechuga de pavo cocida', 80, 220), ('Papa criolla cocida', 100, 280),
    ('Habichuela cocida', 50, 150), ('Aguacate', 30, 90))
add('Tilapia con arroz integral y brócoli', 'cotidiana', M,
    'Sirve la tilapia cocida con arroz integral y brócoli; añade aceite como aderezo.',
    ('Tilapia cocida', 80, 230), ('Arroz integral cocido', 100, 260),
    ('Brócoli cocido', 50, 160), ('Aceite de oliva', 10, 25))
add('Ensalada de lentejas, batata y semillas', 'internacional', M,
    'Mezcla las lentejas con batata cocida y tomate; termina con chía y aceite de oliva.',
    ('Lentejas cocidas', 100, 260), ('Batata (camote) cocida', 80, 240), ('Tomate', 40, 120),
    ('Semillas de chía', 10, 25), ('Aceite de oliva', 5, 20))
add('Tofu con fríjol negro, arroz y pimentón', 'latina', M,
    'Sirve el tofu con fríjoles negros y arroz cocidos; acompaña con pimentón y aguacate.',
    ('Tofu firme', 60, 180), ('Fríjol negro cocido', 80, 220), ('Arroz blanco cocido', 60, 190),
    ('Pimentón', 30, 100), ('Aguacate', 20, 80))
add('Garbanzos con quinua, espinaca y almendras', 'mediterránea inspirada', M,
    'Mezcla los garbanzos, la quinua y la espinaca cocidos; añade almendras y aceite de oliva.',
    ('Garbanzos cocidos', 100, 250), ('Quinua cocida', 60, 190), ('Espinaca cocida', 40, 130),
    ('Almendras', 10, 30), ('Aceite de oliva', 5, 15))

MAX_OFFERED = 36
PROTEIN_PREFER, PROTEIN_REQUIRE, PROTEIN_IGNORE = 'prefer', 'require', 'ignore'


@lru_cache(maxsize=256)
def _fit_portions(name, ingredients, foods, kcal, protein_g):
    """Reutiliza solo cálculos puros, con memoria acotada y claves por composición completa.

    Nunca conserva fichas, modelos Django ni resultados del proveedor. Un cambio en alimentos,
    nutrientes, rangos u objetivos produce otra clave; los filtros de dieta se ejecutan antes.
    Las porciones son inmutables y cada llamada reconstruye sus propias opciones.
    """
    rules = meal.SlotRules(slot_code='', kcal=kcal, protein_g=protein_g, foods={})
    rng = random.Random(f'{VERSION}:{name}:{kcal}:{protein_g}')
    for _ in range(350):
        rows = [(food, rng.randrange(low // 5, high // 5 + 1) * 5)
                for food, (_ingredient, low, high) in zip(foods, ingredients)]
        scaled, _totals, errors = meal.evaluate_option(rows, rules)
        if errors:
            continue
        _checked_rows, checked, recheck_errors = meal.evaluate_option(scaled, rules)
        if recheck_errors or checked.protein < protein_g or checked.fat_share < Decimal('.24'):
            continue
        if any(not Decimal(low) * Decimal('.7') <= grams <= Decimal(high) * Decimal('1.3')
               for (_food, grams), (_name, low, high) in zip(scaled, ingredients)):
            continue
        return tuple(grams for _food, grams in scaled)
    return None


def candidates(rules, cooking='basic', seed=None):
    """Una porción viable por receta; sin sustituciones improvisadas cuando falta un ingrediente.

    Sin `seed` sale en el orden del catálogo; con `seed` (p. ej. el identificador del trabajo) se
    reordena de forma determinista por trabajo y comida, para que ni la IA ni el simulador vean
    siempre los mismos platos primero."""
    by_name = {food.name: food for food in rules.foods.values()}
    options = []
    for name, cuisine, slots, preparation, ingredients, effort, family in RECIPES:
        if rules.slot_code not in slots or (cooking == 'none' and effort != 'none'):
            continue
        if any(ingredient not in by_name for ingredient, _low, _high in ingredients):
            continue
        foods = tuple(by_name[ingredient] for ingredient, _low, _high in ingredients)
        grams = _fit_portions(name, ingredients, foods, rules.kcal, rules.protein_g)
        if grams is not None:
            options.append({
                'name': name, 'cuisine': cuisine, 'main_protein': family, 'preparation': preparation,
                'items': [{'food_id': food.id, 'grams': amount} for food, amount in zip(foods, grams)],
            })
    if seed is not None:
        random.Random(f'{VERSION}:{seed}:{rules.slot_code}').shuffle(options)
    return options


def protein_of(name):
    """Proteína principal de una receta del catálogo por su nombre (None si no es una receta)."""
    return PROTEIN_BY_NAME.get((name or '').casefold())


def _entries(options, existing, avoid_names):
    """Recetas elegibles: sin los nombres a evitar ni los de las opciones existentes, y que no se
    parezcan (Jaccard de alimentos) a ninguna existente."""
    avoid = {name.casefold() for name in avoid_names} | {option.name.casefold() for option in existing}
    entries = [(option, frozenset(item['food_id'] for item in option['items']), option.get('main_protein'))
               for option in options if option['name'].casefold() not in avoid]
    return [(option, ids, protein) for option, ids, protein in entries
            if all(meal._jaccard(ids, other.food_ids) < meal.VARIETY_MAX_OVERLAP for other in existing)]


def _search(entries, count, taken=None):
    """Primer grupo de `count` recetas (en el orden dado) que se parecen poco entre sí. Con `taken`
    (conjunto) tampoco repite proteína principal entre ellas ni con las de `taken`."""
    chosen = []

    def extend(start):
        if len(chosen) == count:
            return True
        for index in range(start, len(entries)):
            _option, ids, protein = entries[index]
            if any(meal._jaccard(ids, other_ids) >= meal.VARIETY_MAX_OVERLAP for _o, other_ids, _p in chosen):
                continue
            if taken is not None and protein is not None and (
                    protein in taken or any(protein == other for _o, _i, other in chosen)):
                continue
            chosen.append(entries[index])
            if extend(index + 1):
                return True
            chosen.pop()
        return False

    return [entry[0] for entry in chosen] if extend(0) else []


def _public(group):
    return [copy.deepcopy({key: value for key, value in option.items() if key not in _STRIPPED})
            for option in group]


def _taken(existing):
    """Proteínas principales de las opciones que ya existen en la comida."""
    return frozenset(filter(None, (protein_of(option.name) for option in existing)))


def _pick(options, count, existing, avoid_names, protein):
    entries = _entries(options, existing, avoid_names)
    if protein != PROTEIN_IGNORE:
        group = _search(entries, count, _taken(existing))
        if group or protein == PROTEIN_REQUIRE:
            return group
    return _search(entries, count)


def select_options(options, count, existing=(), avoid_names=(), protein=PROTEIN_PREFER):
    """Busca un grupo completo con variedad; no deja la elección en un greedy incompleto.

    `protein`: 'prefer' (por omisión) busca primero un grupo cuyas opciones no comparten proteína
    principal y, si no existe, usa la regla de variedad de siempre; 'require' solo acepta el grupo
    con proteínas distintas; 'ignore' no mira la proteína."""
    return _public(_pick(options, count, existing, avoid_names, protein))


def _limit(options):
    """Recorta a `MAX_OFFERED` conservando el orden y repartiendo el cupo entre las proteínas
    principales (una de cada familia por turno), para que el recorte no deje solo pollo."""
    if len(options) <= MAX_OFFERED:
        return options
    queues = {}
    for option in options:
        queues.setdefault(option.get('main_protein'), []).append(option)
    keep = set()
    while len(keep) < MAX_OFFERED:
        for queue in queues.values():
            if queue and len(keep) < MAX_OFFERED:
                keep.add(id(queue.pop(0)))
    return [option for option in options if id(option) in keep]


class Offer(NamedTuple):
    options: list                # recetas que se le ofrecen a la IA (vacía = no hay suficientes)
    distinct_protein: bool       # existe un grupo sin proteína repetida: la validación lo exige


def offer(candidate_options, count, existing=(), avoid_names=()):
    """Qué recetas se ofrecen para una comida.

    1. Nunca las de las opciones existentes ni las que se les parecen demasiado.
    2. Sin los nombres ya usados en el día (`avoid_names`); si así no queda un grupo válido
       (perfiles muy restringidos) vuelve a la lista completa.
    3. Si hay un grupo con proteínas principales distintas, se exige (y, con opciones existentes,
       se ofrecen solo las de otra proteína); si no, se cae a la regla de variedad de siempre.
    4. La lista se recorta a `MAX_OFFERED` sin perder esas garantías."""
    existing_names = {option.name.casefold() for option in existing}
    avoid = {name.casefold() for name in avoid_names}
    base = [option for option in candidate_options if option['name'].casefold() not in existing_names]
    fresh = [option for option in base if option['name'].casefold() not in avoid]
    pool = fresh if _pick(fresh, count, existing, (), PROTEIN_IGNORE) else base
    if not _pick(pool, count, existing, (), PROTEIN_IGNORE):
        return Offer([], False)
    pool = [entry[0] for entry in _entries(pool, existing, ())]      # solo lo que puede elegirse
    taken = _taken(existing)
    spread = [option for option in pool if option.get('main_protein') not in taken]
    if _pick(spread, count, existing, (), PROTEIN_REQUIRE):
        limited = _limit(spread)
        return Offer(limited if _pick(limited, count, existing, (), PROTEIN_REQUIRE) else spread, True)
    limited = _limit(pool)
    return Offer(limited if _pick(limited, count, existing, (), PROTEIN_IGNORE) else pool, False)
