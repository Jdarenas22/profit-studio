from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import AccountAuditLog, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    """Admin de usuarios SIN borrar (docs/contracts/cuentas-eliminar.md, sección 12).

    El botón «Eliminar» y la acción masiva borraban en cascada y se saltaban la lápida, la
    reasignación de clientas y la auditoría. `is_active` e `is_superuser` quedan de solo lectura
    (respuesta 9a): desactivar, reactivar y eliminar se hacen desde el panel.
    """
    list_display = ['username', 'email', 'first_name', 'last_name', 'role', 'is_active', 'date_joined']
    list_filter = ['role', 'is_active', 'date_joined']
    search_fields = ['username', 'email', 'first_name', 'last_name', 'phone']
    ordering = ['-date_joined']
    readonly_fields = ('is_active', 'is_superuser', 'deactivated_at', 'erased_at')

    fieldsets = UserAdmin.fieldsets + (
        ('ProFit Studio', {
            'fields': ('role', 'phone', 'profile_photo', 'deactivated_at', 'erased_at'),
        }),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('ProFit Studio', {
            'fields': ('first_name', 'last_name', 'email', 'role', 'phone'),
        }),
    )

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions


@admin.register(AccountAuditLog)
class AccountAuditLogAdmin(admin.ModelAdmin):
    """Auditoría de cuentas: solo lectura y solo para la superusuaria (respuesta 8a)."""
    list_display = ['created_at', 'action', 'target_role', 'target_id', 'actor_id', 'reason',
                    'reassigned_count']
    list_filter = ['action', 'target_role', 'reason']
    ordering = ['-created_at', '-pk']
    readonly_fields = ['created_at', 'actor', 'target', 'target_role', 'action', 'reason',
                       'reassigned_to', 'reassigned_count']
    fields = readonly_fields
    list_select_related = False

    def has_module_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        return {}
