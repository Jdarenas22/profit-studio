from django.contrib import admin
from .models import ExerciseCategory, Exercise


@admin.register(ExerciseCategory)
class ExerciseCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'order']
    ordering = ['order', 'name']


@admin.register(Exercise)
class ExerciseAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'category', 'level',
        'is_public', 'is_active', 'created_at',
    ]
    list_filter = ['category', 'level', 'is_public', 'is_active']
    search_fields = ['name', 'description', 'muscles']
    list_editable = ['is_public', 'is_active']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = [
        ('Información', {
            'fields': ['name', 'category', 'level', 'description', 'muscles', 'recommendations'],
        }),
        ('Archivos multimedia', {
            'fields': ['video_file', 'image'],
        }),
        ('Configuración', {
            'fields': ['is_public', 'is_active'],
        }),
        ('Registro', {
            'fields': ['created_at', 'updated_at'],
            'classes': ['collapse'],
        }),
    ]
