"""Solo `TrainerCredential` va al admin, y solo para la superusuaria.

Los planes, trabajos y eventos NO se registran: el admin se salta la regla de asignación de
clientes (apps/accounts/permissions.py) y mostraría datos de clientas ajenas.
"""
from django.contrib import admin

from .models import TrainerCredential


@admin.register(TrainerCredential)
class TrainerCredentialAdmin(admin.ModelAdmin):
    list_display = ('user', 'can_validate_nutrition', 'registration_label')
    list_filter = ('can_validate_nutrition',)

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
