"""Catálogo INICIAL de alimentos (valores por 100 g, como se comen: cocidos salvo que diga "crudo").

Son cifras APROXIMADAS de referencia internacional (del orden de USDA FoodData Central) escritas a
mano por el equipo técnico: NO se copió ninguna tabla (la licencia de la tabla del ICBF está por
verificar) y NO están validadas. Un nutricionista-dietista debe revisarlas y marcarlas como
validadas en el admin (Alimentos > acción "Marcar como validados") antes de usarlas con clientas
reales: fuera de DEBUG, el sistema ignora los alimentos sin `validated_by`.

Cargar o actualizar: `python manage.py load_foods` (nunca pisa un alimento ya validado).

Fila: (nombre, grupo, kcal, proteína, carbohidratos, grasa, fibra, medida casera (singular,
plural, gramos), alérgenos, trazas, clase de dieta, etiquetas de condición, es_común)
Clase de dieta: vegan | vegetarian | pescatarian | meat | pork | shellfish.
"""
from decimal import Decimal

SOURCE = 'Valores aproximados de referencia (tipo USDA FoodData Central); pendiente de verificación'

# Dietas en las que cabe cada clase de alimento
DIETS_BY_CLASS = {
    'vegan': ['omnivore', 'vegetarian', 'vegan', 'pescatarian', 'halal', 'kosher'],
    'vegetarian': ['omnivore', 'vegetarian', 'pescatarian', 'halal', 'kosher'],
    'pescatarian': ['omnivore', 'pescatarian', 'halal'],
    'meat': ['omnivore', 'halal', 'kosher'],
    'pork': ['omnivore'],
    'shellfish': ['omnivore', 'pescatarian'],
}

# fmt: off
FOODS = [
    # ── Cereales ──
    ('Arroz blanco cocido', 'cereales', 130, 2.7, 28, 0.3, 0.4, ('taza cocida', 'tazas cocidas', 160), (), (), 'vegan', (), True),
    ('Arroz integral cocido', 'cereales', 123, 2.7, 25.6, 1.0, 1.6, ('taza cocida', 'tazas cocidas', 170), (), (), 'vegan', (), True),
    ('Avena en hojuelas (cruda)', 'cereales', 380, 13, 67, 7, 10, ('taza', 'tazas', 80), (), ('gluten',), 'vegan', (), True),
    ('Arepa de maíz', 'cereales', 175, 4, 36, 2, 2.5, ('arepa mediana', 'arepas medianas', 70), (), (), 'vegan', (), True),
    ('Pan integral tajado', 'cereales', 250, 11, 43, 4, 7, ('tajada', 'tajadas', 28), ('gluten',), (), 'vegan', (), True),
    ('Pan blanco tajado', 'cereales', 265, 9, 49, 3.2, 2.7, ('tajada', 'tajadas', 28), ('gluten',), (), 'vegan', (), True),
    ('Pasta cocida', 'cereales', 158, 5.8, 31, 0.9, 1.8, ('taza cocida', 'tazas cocidas', 140), ('gluten',), (), 'vegan', (), True),
    ('Tortilla de maíz', 'cereales', 218, 5.7, 44, 2.9, 5.2, ('tortilla', 'tortillas', 30), (), (), 'vegan', (), True),
    ('Galletas de soda', 'cereales', 430, 9, 71, 12, 2.5, ('galleta', 'galletas', 5), ('gluten',), (), 'vegan', ('high_sodium',), True),
    ('Quinua cocida', 'cereales', 120, 4.4, 21.3, 1.9, 2.8, ('taza cocida', 'tazas cocidas', 185), (), (), 'vegan', (), False),
    ('Choclo (maíz tierno) cocido', 'cereales', 96, 3.4, 21, 1.5, 2.4, ('taza de granos', 'tazas de granos', 165), (), (), 'vegan', ('fodmap',), True),
    # ── Tubérculos y plátanos ──
    ('Papa criolla cocida', 'tuberculos', 85, 2, 19.5, 0.1, 1.8, ('papa criolla', 'papas criollas', 30), (), (), 'vegan', (), True),
    ('Papa pastusa cocida', 'tuberculos', 87, 1.9, 20.1, 0.1, 1.8, ('papa mediana', 'papas medianas', 130), (), (), 'vegan', (), True),
    ('Yuca cocida', 'tuberculos', 160, 1.4, 38, 0.3, 1.8, ('trozo mediano', 'trozos medianos', 100), (), (), 'vegan', (), True),
    ('Plátano maduro asado o cocido', 'tuberculos', 122, 1, 31.9, 0.4, 2.3, ('trozo mediano', 'trozos medianos', 80), (), (), 'vegan', (), True),
    ('Plátano verde cocido', 'tuberculos', 120, 1.3, 31, 0.3, 2.3, ('trozo mediano', 'trozos medianos', 80), (), (), 'vegan', (), True),
    ('Arracacha cocida', 'tuberculos', 105, 0.9, 25, 0.2, 2, ('trozo mediano', 'trozos medianos', 80), (), (), 'vegan', (), True),
    ('Ñame cocido', 'tuberculos', 116, 1.5, 27.5, 0.1, 3.9, ('trozo mediano', 'trozos medianos', 100), (), (), 'vegan', (), False),
    ('Batata (camote) cocida', 'tuberculos', 90, 2, 20.7, 0.2, 3.3, ('batata mediana', 'batatas medianas', 130), (), (), 'vegan', (), True),
    # ── Verduras ──
    ('Ahuyama cocida', 'verduras', 20, 0.7, 4.9, 0.1, 1.1, ('taza', 'tazas', 245), (), (), 'vegan', (), True),
    ('Tomate', 'verduras', 18, 0.9, 3.9, 0.2, 1.2, ('tomate mediano', 'tomates medianos', 120), (), (), 'vegan', ('reflux_trigger',), True),
    ('Cebolla cabezona', 'verduras', 40, 1.1, 9.3, 0.1, 1.7, ('cebolla mediana', 'cebollas medianas', 110), (), (), 'vegan', ('fodmap', 'reflux_trigger'), True),
    ('Lechuga', 'verduras', 15, 1.4, 2.9, 0.2, 1.3, ('taza', 'tazas', 50), (), (), 'vegan', (), True),
    ('Zanahoria cruda', 'verduras', 41, 0.9, 9.6, 0.2, 2.8, ('zanahoria mediana', 'zanahorias medianas', 60), (), (), 'vegan', (), True),
    ('Pepino cohombro', 'verduras', 15, 0.7, 3.6, 0.1, 0.5, ('taza en rodajas', 'tazas en rodajas', 120), (), (), 'vegan', (), True),
    ('Brócoli cocido', 'verduras', 35, 2.4, 7.2, 0.4, 3.3, ('taza', 'tazas', 155), (), (), 'vegan', ('fodmap',), True),
    ('Espinaca cocida', 'verduras', 23, 3, 3.8, 0.3, 2.4, ('taza', 'tazas', 180), (), (), 'vegan', (), True),
    ('Habichuela cocida', 'verduras', 35, 1.9, 7.9, 0.3, 3.2, ('taza', 'tazas', 125), (), (), 'vegan', (), True),
    ('Repollo crudo', 'verduras', 25, 1.3, 5.8, 0.1, 2.5, ('taza picada', 'tazas picadas', 90), (), (), 'vegan', ('fodmap',), True),
    ('Remolacha cocida', 'verduras', 44, 1.7, 10, 0.2, 2, ('remolacha mediana', 'remolachas medianas', 100), (), (), 'vegan', ('fodmap',), True),
    ('Pimentón', 'verduras', 31, 1, 6, 0.3, 2.1, ('pimentón mediano', 'pimentones medianos', 120), (), (), 'vegan', (), True),
    ('Coliflor cocida', 'verduras', 23, 1.8, 4.1, 0.5, 2.3, ('taza', 'tazas', 125), (), (), 'vegan', ('fodmap',), True),
    ('Champiñones', 'verduras', 22, 3.1, 3.3, 0.3, 1, ('taza', 'tazas', 70), (), (), 'vegan', ('fodmap',), False),
    ('Calabacín cocido', 'verduras', 15, 1.1, 2.7, 0.4, 1, ('taza', 'tazas', 180), (), (), 'vegan', (), True),
    # ── Frutas ──
    ('Banano', 'frutas', 89, 1.1, 22.8, 0.3, 2.6, ('banano mediano', 'bananos medianos', 120), (), (), 'vegan', (), True),
    ('Manzana', 'frutas', 52, 0.3, 13.8, 0.2, 2.4, ('manzana mediana', 'manzanas medianas', 180), (), (), 'vegan', ('fodmap',), True),
    ('Naranja', 'frutas', 47, 0.9, 11.8, 0.1, 2.4, ('naranja mediana', 'naranjas medianas', 130), (), (), 'vegan', ('reflux_trigger',), True),
    ('Mandarina', 'frutas', 53, 0.8, 13.3, 0.3, 1.8, ('mandarina', 'mandarinas', 90), (), (), 'vegan', ('reflux_trigger',), True),
    ('Papaya', 'frutas', 43, 0.5, 10.8, 0.3, 1.7, ('taza en cubos', 'tazas en cubos', 140), (), (), 'vegan', (), True),
    ('Piña', 'frutas', 50, 0.5, 13.1, 0.1, 1.4, ('taza en cubos', 'tazas en cubos', 165), (), (), 'vegan', ('reflux_trigger',), True),
    ('Mango', 'frutas', 60, 0.8, 15, 0.4, 1.6, ('taza en cubos', 'tazas en cubos', 165), (), (), 'vegan', ('fodmap',), True),
    ('Fresa', 'frutas', 32, 0.7, 7.7, 0.3, 2, ('taza', 'tazas', 150), (), (), 'vegan', (), True),
    ('Mora', 'frutas', 43, 1.4, 9.6, 0.5, 5.3, ('taza', 'tazas', 145), (), (), 'vegan', ('fodmap',), True),
    ('Sandía', 'frutas', 30, 0.6, 7.6, 0.2, 0.4, ('taza en cubos', 'tazas en cubos', 150), (), (), 'vegan', ('fodmap',), True),
    ('Melón', 'frutas', 34, 0.8, 8.2, 0.2, 0.9, ('taza en cubos', 'tazas en cubos', 160), (), (), 'vegan', (), True),
    ('Pera', 'frutas', 57, 0.4, 15.2, 0.1, 3.1, ('pera mediana', 'peras medianas', 180), (), (), 'vegan', ('fodmap',), True),
    ('Uvas', 'frutas', 69, 0.7, 18.1, 0.2, 0.9, ('taza', 'tazas', 150), (), (), 'vegan', (), True),
    ('Guayaba', 'frutas', 68, 2.6, 14.3, 1, 5.4, ('guayaba mediana', 'guayabas medianas', 90), (), (), 'vegan', (), True),
    ('Kiwi', 'frutas', 61, 1.1, 14.7, 0.5, 3, ('kiwi', 'kiwis', 75), (), (), 'vegan', (), False),
    # ── Lácteos y alternativas ──
    ('Leche entera', 'lacteos', 61, 3.2, 4.8, 3.3, 0, ('vaso', 'vasos', 240), ('milk',), (), 'vegetarian', (), True),
    ('Leche semidescremada', 'lacteos', 50, 3.3, 4.8, 2, 0, ('vaso', 'vasos', 240), ('milk',), (), 'vegetarian', (), True),
    ('Leche descremada', 'lacteos', 34, 3.4, 5, 0.1, 0, ('vaso', 'vasos', 240), ('milk',), (), 'vegetarian', (), True),
    ('Yogur natural sin azúcar', 'lacteos', 61, 3.5, 4.7, 3.3, 0, ('pote', 'potes', 170), ('milk',), (), 'vegetarian', (), True),
    ('Yogur griego natural', 'lacteos', 97, 9, 3.9, 5, 0, ('pote', 'potes', 150), ('milk',), (), 'vegetarian', (), False),
    ('Queso fresco campesino', 'lacteos', 299, 18.1, 2.9, 23.8, 0, ('tajada', 'tajadas', 30), ('milk',), (), 'vegetarian', ('high_sodium',), True),
    ('Queso mozzarella semidescremado', 'lacteos', 254, 24.3, 2.8, 15.9, 0, ('tajada', 'tajadas', 25), ('milk',), (), 'vegetarian', ('high_sodium',), False),
    ('Queso cottage', 'lacteos', 98, 11.1, 3.4, 4.3, 0, ('taza', 'tazas', 225), ('milk',), (), 'vegetarian', ('high_sodium',), False),
    ('Bebida de soya sin azúcar', 'lacteos', 33, 2.9, 1.7, 1.8, 0.4, ('vaso', 'vasos', 240), ('soy',), (), 'vegan', ('fodmap',), False),
    ('Bebida de almendras sin azúcar', 'lacteos', 15, 0.6, 0.3, 1.1, 0.2, ('vaso', 'vasos', 240), ('tree_nut',), ('peanut',), 'vegan', (), False),
    # ── Carnes, pescados y huevo ──
    ('Pechuga de pollo cocida sin piel', 'proteina_animal', 165, 31, 0, 3.6, 0, ('filete mediano', 'filetes medianos', 120), (), (), 'meat', (), True),
    ('Muslo de pollo cocido sin piel', 'proteina_animal', 179, 24.8, 0, 8.2, 0, ('muslo', 'muslos', 80), (), (), 'meat', (), True),
    ('Carne de res magra cocida', 'proteina_animal', 185, 27.5, 0, 7.8, 0, ('porción', 'porciones', 100), (), (), 'meat', ('high_purine',), True),
    ('Carne molida de res magra cocida', 'proteina_animal', 217, 26.1, 0, 11.7, 0, ('porción', 'porciones', 100), (), (), 'meat', ('high_purine',), True),
    ('Lomo de cerdo magro cocido', 'proteina_animal', 190, 27, 0, 8.7, 0, ('porción', 'porciones', 100), (), (), 'pork', (), True),
    ('Pechuga de pavo cocida', 'proteina_animal', 135, 30, 0, 1, 0, ('porción', 'porciones', 100), (), (), 'meat', (), False),
    ('Tilapia cocida', 'proteina_animal', 128, 26.2, 0, 2.7, 0, ('filete', 'filetes', 110), ('fish',), (), 'pescatarian', (), True),
    ('Atún en agua (escurrido)', 'proteina_animal', 116, 25.5, 0, 0.8, 0, ('lata escurrida', 'latas escurridas', 110), ('fish',), (), 'pescatarian', ('high_sodium', 'high_purine'), True),
    ('Salmón cocido', 'proteina_animal', 206, 22.1, 0, 12.4, 0, ('filete', 'filetes', 130), ('fish',), (), 'pescatarian', (), False),
    ('Camarón cocido', 'proteina_animal', 99, 24, 0.2, 0.3, 0, ('porción', 'porciones', 100), ('shellfish',), (), 'shellfish', ('high_purine',), False),
    ('Huevo entero cocido', 'proteina_animal', 155, 12.6, 1.1, 10.6, 0, ('huevo', 'huevos', 50), ('egg',), (), 'vegetarian', (), True),
    ('Clara de huevo cocida', 'proteina_animal', 52, 10.9, 0.7, 0.2, 0, ('clara', 'claras', 33), ('egg',), (), 'vegetarian', (), True),
    # ── Leguminosas ──
    ('Fríjol rojo cocido', 'leguminosas', 127, 8.7, 22.8, 0.5, 6.4, ('taza cocida', 'tazas cocidas', 180), (), (), 'vegan', ('fodmap',), True),
    ('Fríjol negro cocido', 'leguminosas', 132, 8.9, 23.7, 0.5, 8.7, ('taza cocida', 'tazas cocidas', 170), (), (), 'vegan', ('fodmap',), True),
    ('Lentejas cocidas', 'leguminosas', 116, 9, 20.1, 0.4, 7.9, ('taza cocida', 'tazas cocidas', 200), (), (), 'vegan', ('fodmap',), True),
    ('Garbanzos cocidos', 'leguminosas', 164, 8.9, 27.4, 2.6, 7.6, ('taza cocida', 'tazas cocidas', 165), (), (), 'vegan', ('fodmap',), True),
    ('Arveja verde cocida', 'leguminosas', 84, 5.4, 15.6, 0.2, 5.5, ('taza cocida', 'tazas cocidas', 160), (), (), 'vegan', ('fodmap',), True),
    ('Tofu firme', 'leguminosas', 144, 17.3, 2.8, 8.7, 2.3, ('porción', 'porciones', 100), ('soy',), (), 'vegan', (), False),
    ('Hummus', 'leguminosas', 166, 7.9, 14.3, 9.6, 6, ('cucharada', 'cucharadas', 15), ('sesame',), (), 'vegan', ('fodmap',), False),
    # ── Grasas, nueces y semillas ──
    ('Aceite de oliva', 'grasas', 884, 0, 0, 100, 0, ('cucharadita', 'cucharaditas', 4.5), (), (), 'vegan', (), True),
    ('Aceite vegetal (girasol o canola)', 'grasas', 884, 0, 0, 100, 0, ('cucharadita', 'cucharaditas', 4.5), (), (), 'vegan', (), True),
    ('Aguacate', 'grasas', 160, 2, 8.5, 14.7, 6.7, ('aguacate mediano', 'aguacates medianos', 150), (), (), 'vegan', ('fodmap',), True),
    ('Almendras', 'grasas', 579, 21.2, 21.6, 49.9, 12.5, ('puñado', 'puñados', 28), ('tree_nut',), ('peanut',), 'vegan', (), False),
    ('Maní tostado sin sal', 'grasas', 585, 23.7, 21.5, 49.7, 8, ('puñado', 'puñados', 28), ('peanut',), ('tree_nut',), 'vegan', (), True),
    ('Nueces', 'grasas', 654, 15.2, 13.7, 65.2, 6.7, ('puñado', 'puñados', 28), ('tree_nut',), ('peanut',), 'vegan', (), False),
    ('Semillas de chía', 'grasas', 486, 16.5, 42.1, 30.7, 34.4, ('cucharada', 'cucharadas', 12), (), (), 'vegan', (), False),
    ('Linaza molida', 'grasas', 534, 18.3, 28.9, 42.2, 27.3, ('cucharada', 'cucharadas', 10), (), (), 'vegan', (), False),
    ('Mantequilla de maní sin azúcar', 'grasas', 588, 25, 20, 50, 6, ('cucharada', 'cucharadas', 16), ('peanut',), ('tree_nut',), 'vegan', (), True),
    ('Mantequilla', 'grasas', 717, 0.9, 0.1, 81, 0, ('cucharadita', 'cucharaditas', 5), ('milk',), (), 'vegetarian', (), True),
    # ── Azúcares y dulces ──
    ('Miel de abeja', 'azucares', 304, 0.3, 82.4, 0, 0.2, ('cucharadita', 'cucharaditas', 7), (), (), 'vegetarian', ('high_free_sugar', 'fodmap'), True),
    ('Chocolate oscuro 70 %', 'azucares', 598, 7.8, 45.9, 42.6, 10.9, ('cuadrito', 'cuadritos', 10), (), ('milk',), 'vegan', ('reflux_trigger',), False),
]
# fmt: on


def food_rows():
    """Filas listas para `Food.objects.update_or_create` (valores como `Decimal`)."""
    rows = []
    for (name, group, kcal, protein, carbs, fat, fiber, household, allergens, traces, diet_class,
         conditions, common) in FOODS:
        unit, plural, grams = household
        rows.append({
            'name': name, 'group': group,
            'kcal100': Decimal(str(kcal)), 'protein100': Decimal(str(protein)),
            'carbs100': Decimal(str(carbs)), 'fat100': Decimal(str(fat)), 'fiber100': Decimal(str(fiber)),
            'household_unit': unit, 'household_unit_plural': plural, 'household_grams': Decimal(str(grams)),
            'allergen_tags': list(allergens), 'trace_tags': list(traces),
            'diet_tags': list(DIETS_BY_CLASS[diet_class]), 'condition_tags': list(conditions),
            'is_common': common, 'source': SOURCE,
        })
    return rows
