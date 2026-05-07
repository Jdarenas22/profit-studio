from django.contrib import admin
from .models import InitialAssessment, DixonTest


class DixonTestInline(admin.StackedInline):
    model = DixonTest
    extra = 0
    readonly_fields = ['index_value', 'classification']
    fields = ['p0', 'p1', 'p2', 'index_value', 'classification', 'observations']


@admin.register(InitialAssessment)
class InitialAssessmentAdmin(admin.ModelAdmin):
    list_display = [
        'user', 'date', 'age', 'sex',
        'weight', 'height', 'imc', 'imc_classification',
    ]
    list_filter = ['sex', 'imc_classification', 'date']
    search_fields = ['user__username', 'user__first_name', 'user__last_name']
    readonly_fields = ['imc', 'imc_classification', 'date']
    inlines = [DixonTestInline]
    raw_id_fields = ['user', 'trainer']


@admin.register(DixonTest)
class DixonTestAdmin(admin.ModelAdmin):
    list_display = ['assessment', 'p0', 'p1', 'p2', 'index_value', 'classification']
    readonly_fields = ['index_value', 'classification']
    search_fields = ['assessment__user__username', 'assessment__user__first_name']
