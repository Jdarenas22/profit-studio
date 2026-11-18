"""Importación explícita: no empareja por nombre ni sobrescribe registros revisados."""
from django.db import transaction
from django.utils import timezone

from ..models import Food, FoodImportRevision
from .fdc import FDCError
from .fdc_normalize import normalize_food

STATES = ('raw', 'cooked', 'as_sold', 'prepared')


def import_food(payload, *, food_id=None, name=None, group=None, state, confirm_description, apply=False):
    identifier = payload.get('fdcId') if isinstance(payload, dict) else None
    values, snapshot, digest = normalize_food(payload, identifier)
    if state not in STATES:
        raise FDCError('fdc_preparation_state_required')
    if confirm_description != snapshot['description'] and apply:
        raise FDCError('fdc_confirm_description_required')
    if bool(food_id) == bool(name):
        raise FDCError('fdc_choose_food_id_or_new_name')
    if name and (not isinstance(name, str) or not 1 <= len(name.strip()) <= 120):
        raise FDCError('fdc_invalid_local_name')
    if name and group not in dict(Food._meta.get_field('group').choices):
        raise FDCError('fdc_invalid_group')
    with transaction.atomic():
        food = Food.objects.select_for_update().filter(pk=food_id).first() if food_id else None
        if food_id and food is None:
            raise FDCError('fdc_local_food_missing')
        if name and Food.objects.filter(name=name.strip()).exists():
            raise FDCError('fdc_name_exists_use_food_id')
        if food and food.fdc_id == identifier and food.source_hash == digest and food.preparation_state == state:
            return food, snapshot, False
        if food and food.validated_by:
            raise FDCError('fdc_reviewed_food_not_overwritten')
        if food and food.fdc_id and food.fdc_id != identifier:
            raise FDCError('fdc_different_source_binding')
        if food and food.preparation_state not in ('unknown', state):
            raise FDCError('fdc_preparation_state_mismatch')
        if not apply:
            return food, snapshot, False
        if food is None:
            food = Food(name=name.strip(), group=group)
        for field, value in values.items():
            setattr(food, field, value)
        food.fdc_id = identifier
        food.preparation_state = state
        food.source_snapshot = snapshot
        food.source_hash = digest
        food.source_imported_at = timezone.now()
        food.source = f'USDA FoodData Central / {identifier}'
        food.validated_by = ''
        food.is_active = False   # Revisar identidad, dieta y alérgenos en el admin antes de usar.
        food.full_clean()
        food.save()
        FoodImportRevision.objects.get_or_create(food=food, source_hash=digest, defaults={'snapshot': snapshot})
        return food, snapshot, True

