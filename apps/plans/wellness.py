"""Alcance pausado: no generar ni publicar dietas para tratar condiciones clínicas."""
OUT_OF_SCOPE = 'NUTRITION_CLINICAL_PAUSED'


def outside_scope(profile):
    if profile is None:
        return False
    return bool(
        profile.conditions or profile.medications
        or profile.pregnancy_status not in ('none', '')
        or profile.eating_disorder_history not in ('no', '')
    )
