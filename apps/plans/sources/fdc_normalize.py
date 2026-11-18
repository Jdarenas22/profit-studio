"""Normaliza Food Details (formato full, por 100 g) sin usar labelNutrients por porción."""
import hashlib
import json
from decimal import Decimal, InvalidOperation

from .fdc import FDCError

# IDs oficiales del objeto nutrient.id; no confundir con foodNutrient.id o nutrient.number.
NUTRIENTS = {
    1008: ('kcal100', 'kcal'), 1003: ('protein100', 'g'), 1004: ('fat100', 'g'),
    1005: ('carbs100', 'g'), 1079: ('fiber100', 'g'), 2000: ('sugars100', 'g'),
    1235: ('added_sugars100', 'g'), 1093: ('sodium_mg100', 'mg'),
    1258: ('saturated_fat100', 'g'),
}
OPTIONAL_FIELDS = ('fiber100', 'sugars100', 'added_sugars100', 'sodium_mg100', 'saturated_fat100')
SUPPORTED_TYPES = ('Foundation', 'SR Legacy', 'Branded', 'Survey (FNDDS)')


def _amount(value, maximum):
    if isinstance(value, bool) or value is None:
        raise FDCError('fdc_invalid_nutrient_amount')
    try:
        number = Decimal(str(value))
        if not number.is_finite() or not 0 <= number <= maximum:
            raise FDCError('fdc_invalid_nutrient_amount')
        return number.quantize(Decimal('.001'))
    except (InvalidOperation, ValueError, TypeError):
        raise FDCError('fdc_invalid_nutrient_amount') from None


def normalize_food(payload, expected_id):
    if not isinstance(payload, dict) or type(payload.get('fdcId')) is not int or payload['fdcId'] != expected_id:
        raise FDCError('fdc_id_mismatch')
    if payload.get('dataType') not in SUPPORTED_TYPES:
        raise FDCError('fdc_unsupported_type')
    if payload['dataType'] == 'Branded':
        # Algunas etiquetas de líquidos se normalizan por 100 ml. Sin densidad verificada
        # no convertir esos datos a 100 g ni asumir que 1 ml equivale a 1 g.
        if str(payload.get('servingSizeUnit', '')).lower() not in ('g', 'grm'):
            raise FDCError('fdc_volume_basis_unsupported')
    description = payload.get('description')
    if not isinstance(description, str) or not description.strip():
        raise FDCError('fdc_description_missing')
    rows = payload.get('foodNutrients')
    if not isinstance(rows, list):
        raise FDCError('fdc_nutrients_missing')
    values = {name: None for name in OPTIONAL_FIELDS}
    evidence = {}
    energy_alternatives = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('nutrient'), dict):
            continue
        nutrient = row['nutrient']
        identifier = nutrient.get('id')
        if identifier not in NUTRIENTS and identifier not in (2047, 2048, 1063):
            continue
        if identifier == 1063:
            field, unit = 'sugars100', 'g'
        elif identifier in (2047, 2048):
            field, unit = 'kcal100', 'kcal'
        else:
            field, unit = NUTRIENTS[identifier]
        if str(nutrient.get('unitName', '')).lower() != unit:
            raise FDCError('fdc_unexpected_unit')
        # Falta de análisis no equivale a cero; no inferir desde etiqueta por porción.
        if row.get('amount') is None:
            continue
        number = _amount(row['amount'], Decimal('100000') if field == 'sodium_mg100'
                         else Decimal('1000') if field == 'kcal100' else Decimal('100'))
        detail = {'nutrient_id': identifier, 'unit': unit, 'amount': str(number),
                  'derivation': str((row.get('foodNutrientDerivation') or {}).get('code', ''))[:40]}
        if identifier in (2047, 2048):
            energy_alternatives[identifier] = (number, detail)
            continue
        # 2000 es el nutriente total utilizado en el catálogo; 1063 solo como respaldo.
        if field in evidence and field == 'sugars100' and identifier == 1063:
            continue
        if field in evidence and evidence[field]['nutrient_id'] == identifier and values[field] != number:
            raise FDCError('fdc_conflicting_nutrients')
        if field in evidence and field != 'sugars100' and values[field] != number:
            raise FDCError('fdc_conflicting_nutrients')
        values[field] = number
        evidence[field] = detail
    if 'kcal100' not in evidence:
        alternative = energy_alternatives.get(2048) or energy_alternatives.get(2047)
        if alternative:
            values['kcal100'], evidence['kcal100'] = alternative
    if any(field not in evidence for field in ('kcal100', 'protein100', 'carbs100', 'fat100')):
        raise FDCError('fdc_core_nutrients_missing')
    if values['added_sugars100'] is not None and values['sugars100'] is not None:
        if values['added_sugars100'] > values['sugars100']:
            raise FDCError('fdc_inconsistent_sugars')
    if values['saturated_fat100'] is not None and values['saturated_fat100'] > values['fat100']:
        raise FDCError('fdc_inconsistent_fat')
    snapshot = {
        'provider': 'USDA FoodData Central', 'fdc_id': expected_id, 'basis': '100g',
        'description': description.strip(), 'data_type': payload['dataType'],
        'brand': str(payload.get('brandOwner') or payload.get('brandName') or '')[:160],
        'gtin_upc': str(payload.get('gtinUpc') or '')[:40],
        'market_country': str(payload.get('marketCountry') or '')[:100],
        'ingredients_statement': str(payload.get('ingredients') or '')[:10000],
        'publication_date': str(payload.get('publicationDate') or '')[:40],
        'nutrients': {key: str(value) if value is not None else None for key, value in values.items()},
        'evidence': evidence,
    }
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return values, snapshot, digest

