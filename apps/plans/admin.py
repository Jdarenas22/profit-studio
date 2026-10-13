"""Solo `TrainerCredential` y `Food` (el catálogo de alimentos) van al admin, y solo para la superusuaria.

Los planes, trabajos y eventos NO se registran: el admin se salta la regla de asignación de
clientes (apps/accounts/permissions.py) y mostraría datos de clientas ajenas.
"""
from django.contrib import admin, messages
from django.utils import timezone

from .models import Food, TrainerCredential


class SuperuserOnlyAdmin(admin.ModelAdmin):
    def _is_superuser(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_module_permission(self, request):
        return self._is_superuser(request)

    def has_view_permission(self, request, obj=None):
        return self._is_superuser(request)

    def has_add_permission(self, request):
        return self._is_superuser(request)

    def has_change_permission(self, request, obj=None):
        return self._is_superuser(request)

    def has_delete_permission(self, request, obj=None):
        return self._is_superuser(request)


@admin.register(TrainerCredential)
class TrainerCredentialAdmin(SuperuserOnlyAdmin):
    list_display = ('user', 'can_validate_nutrition', 'registration_label')
    list_filter = ('can_validate_nutrition',)


@admin.register(Food)
class FoodAdmin(SuperuserOnlyAdmin):
    """Catálogo de alimentos. Un alimento sin `validated_by` solo se usa en DEBUG: quien verifica
    las cifras (nutricionista-dietista) lo marca aquí."""
    list_display = ('name', 'group', 'kcal100', 'protein100', 'carbs100', 'fat100', 'is_active', 'validated_by')
    list_filter = ('group', 'is_active', 'is_common')
    search_fields = ('name',)
    actions = ('mark_validated', 'clear_validation')

    @admin.action(description='Marcar como validados (a nombre de quien lo hace)')
    def mark_validated(self, request, queryset):
        who = request.user.get_full_name() or request.user.username
        label = f'{who}, {timezone.localdate():%d/%m/%Y}'
        count = queryset.update(validated_by=label)
        self.message_user(request, f'{count} alimentos marcados como validados por {label}.', messages.SUCCESS)

    @admin.action(description='Quitar la validación')
    def clear_validation(self, request, queryset):
        count = queryset.update(validated_by='')
        self.message_user(request, f'{count} alimentos quedaron sin validar.', messages.WARNING)
