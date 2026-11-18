"""Resumen de la rutina asignada, sin nombres, notas ni identificadores personales."""
from apps.routines.models import Routine


def assigned_training_summary(client):
    routine = Routine.objects.filter(user=client, is_active=True).order_by('-created_at', '-pk').first()
    if routine is None:
        return {'source': 'declared_profile', 'assigned': False}
    days = []
    for day in routine.days.order_by('order', 'pk').prefetch_related('exercises__exercise')[:7]:
        items = list(day.exercises.all())[:30]
        intensity = {}
        for item in items:
            code = item.exercise.intensity
            if code in ('low', 'moderate', 'high'):
                intensity[code] = intensity.get(code, 0) + 1
        days.append({'exercise_count': len(items), 'total_sets': sum(item.sets for item in items),
                     'intensity_counts': intensity})
    return {'source': 'assigned_routine', 'assigned': True, 'days_per_cycle': len(days), 'days': days,
            'energy_basis': 'declared_activity_not_measured_expenditure'}
