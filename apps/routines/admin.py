from django.contrib import admin
from .models import Routine, RoutineDay, RoutineExercise


class RoutineExerciseInline(admin.TabularInline):
    model = RoutineExercise
    extra = 1
    fields = ['exercise', 'sets', 'reps', 'rest_seconds', 'observations', 'order']
    autocomplete_fields = ['exercise']


class RoutineDayInline(admin.StackedInline):
    model = RoutineDay
    extra = 1
    fields = ['name', 'order']


@admin.register(Routine)
class RoutineAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'trainer', 'is_active', 'created_at']
    list_filter = ['is_active', 'trainer']
    search_fields = ['name', 'user__username', 'user__first_name', 'user__last_name']
    raw_id_fields = ['user', 'trainer']
    inlines = [RoutineDayInline]
    readonly_fields = ['created_at', 'updated_at']


@admin.register(RoutineDay)
class RoutineDayAdmin(admin.ModelAdmin):
    list_display = ['routine', 'name', 'order']
    inlines = [RoutineExerciseInline]


@admin.register(RoutineExercise)
class RoutineExerciseAdmin(admin.ModelAdmin):
    list_display = ['exercise', 'day', 'sets', 'reps', 'rest_seconds', 'order']
    search_fields = ['exercise__name', 'day__routine__name']
