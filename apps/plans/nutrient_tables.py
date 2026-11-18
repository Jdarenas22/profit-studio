"""Tablas por receta: Decimal, datos ausentes explícitos y fuentes congeladas al guardar."""
from decimal import Decimal

from django.utils import timezone

from .models import Food
from .rules.foods import items_totals, Totals, round_totals

PER100 = ('kcal100', 'protein100', 'carbs100', 'fat100', 'fiber100',
          'sugars100', 'added_sugars100', 'saturated_fat100', 'sodium_mg100')
EXTRA = ('fiber', 'sugars', 'added_sugars', 'saturated_fat', 'sodium_mg')


def capture(items, expected_totals=None):
    foods = Food.objects.in_bulk([item['food_id'] for item in items])
    rows, ingredients = [], []
    for item in items:
        food = foods[item['food_id']]
        rows.append((food, item['grams']))
        ingredients.append({
            'food_id': food.pk, 'food_name': food.name, 'grams': item['grams'],
            'basis': '100g', 'state': food.preparation_state,
            'source': food.source, 'fdc_id': food.fdc_id, 'source_hash': food.source_hash,
            'per100': {key: str(getattr(food, key)) if getattr(food, key) is not None else None for key in PER100},
        })
    total = items_totals(rows)
    if expected_totals is not None:
        # El catálogo puede actualizarse entre generar una receta y confirmar la transacción.
        if any(getattr(total, name) != getattr(expected_totals, name)
               for name in ('kcal', 'protein', 'carbs', 'fat', *EXTRA)):
            raise ValueError('nutrient_catalog_changed')
    return {
        'format': 'profit-nutrients/1', 'saved_at': timezone.now().isoformat(),
        'units': {'kcal': 'kcal', 'sodium_mg': 'mg', 'other_nutrients': 'g'},
        'ingredients': ingredients,
        'totals': {name: str(getattr(total, name)) if getattr(total, name) is not None else None
                   for name in ('kcal', 'protein', 'carbs', 'fat', *EXTRA)},
    }


def option_totals(option):
    extra = (option.nutrient_snapshot or {}).get('totals', {})
    return Totals(option.kcal, option.protein_g, option.carbs_g, option.fat_g,
                  *(Decimal(str(extra[key])) if extra.get(key) is not None else None for key in EXTRA))


def option_table(option):
    return round_totals(option_totals(option))


def compare_limits(total, limits):
    """Límites aportados por el llamador, en las mismas unidades. Sin inferir una enfermedad.
    Devuelve estado desconocido si falta un dato; nunca afirma cumplimiento con una suma parcial.
    """
    result = {}
    for nutrient, maximum in limits.items():
        if nutrient not in ('kcal', 'protein', 'carbs', 'fat', *EXTRA):
            raise ValueError('Nutriente no admitido.')
        bound = Decimal(str(maximum))
        if not bound.is_finite() or bound < 0:
            raise ValueError('Límite inválido.')
        value = getattr(total, nutrient)
        result[nutrient] = {'status': 'unknown' if value is None else 'above' if value > bound else 'within',
                            'value': str(value) if value is not None else None, 'limit': str(bound)}
    return result

