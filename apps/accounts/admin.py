import logging

from django.contrib import admin, messages
from django.contrib.admin.options import IS_POPUP_VAR
from django.contrib.admin.utils import unquote
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import reverse

from . import choices, services
from .models import AccountAuditLog, User

logger = logging.getLogger(__name__)

# Por qué el admin no puede llevar a una cuenta al flujo de eliminación (texto para la pantalla).
ERASE_BLOCKED_MESSAGES = {
    'account_protected': 'No se puede eliminar una cuenta de superusuaria (tampoco la tuya). Si quieres '
                         'cambiar quién administra, usa «Transferir la administración» en el panel.',
    'account_self': 'No puedes eliminar tu propia cuenta.',
    'account_erased': 'Esa cuenta ya fue eliminada.',
}
ERASE_ONE_AT_A_TIME_MESSAGE = ('Elige una sola cuenta para eliminarla. Se elimina de a una, '
                               'con motivo y confirmación en el panel.')


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    """Admin de usuarios: «Eliminar» lleva al flujo seguro del panel y NUNCA borra aquí
    (docs/contracts/cuentas-eliminar.md, sección 12).

    El borrado propio del admin (botón y acción masiva) es en cascada y se salta la lápida, la
    reasignación de clientas y la auditoría. Por eso:

    - La superusuaria ve «Eliminar», pero `delete_view` y la acción «Eliminar (flujo seguro del
      panel)» solo REDIRIGEN a `trainer_client_delete` o `trainer_staff_delete` (motivo,
      reasignación, confirmación y auditoría en `services.erase_account`).
    - `delete_selected` no existe y `delete_model` / `delete_queryset` fallan: ninguna otra ruta del
      admin puede borrar una cuenta.

    `is_active` e `is_superuser` quedan de solo lectura (respuesta 9a): desactivar, reactivar y
    eliminar se hacen desde el panel.
    """
    list_display = ['username', 'email', 'first_name', 'last_name', 'role', 'is_active', 'date_joined']
    list_filter = ['role', 'is_active', 'date_joined']
    search_fields = ['username', 'email', 'first_name', 'last_name', 'phone']
    ordering = ['-date_joined']
    readonly_fields = ('is_active', 'is_superuser', 'deactivated_at', 'erased_at', 'email_verified_at')
    actions = ['erase_via_panel']

    fieldsets = UserAdmin.fieldsets + (
        ('ProFit Studio', {
            'fields': ('role', 'phone', 'profile_photo', 'deactivated_at', 'erased_at', 'email_verified_at'),
        }),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('ProFit Studio', {
            'fields': ('first_name', 'last_name', 'email', 'role', 'phone'),
        }),
    )

    # ─── Eliminar: solo hacia el flujo seguro del panel ──────────────────────────

    @staticmethod
    def _is_owner(user):
        """Superusuaria activa con rol de entrenadora (la dueña)."""
        return bool(user.is_authenticated and user.is_active and user.is_superuser
                    and user.is_trainer and not user.is_erased)

    def has_delete_permission(self, request, obj=None):
        """«Eliminar» aparece solo para la superusuaria y, sobre una cuenta concreta, solo si el
        servicio la deja eliminar (no superusuaria, no la propia, no una lápida)."""
        if not self._is_owner(request.user):
            return False
        if obj is None:
            return True
        return services.can_act(request.user, obj, choices.ACTION_ERASE)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions

    def delete_model(self, request, obj):
        raise PermissionDenied   # defensa en profundidad: el admin nunca borra una cuenta

    def delete_queryset(self, request, queryset):
        raise PermissionDenied

    @staticmethod
    def _panel_erase_url(obj):
        name = 'trainer_staff_delete' if obj.is_trainer else 'trainer_client_delete'
        return reverse(name, args=[obj.pk])

    def _redirect_to_safe_erase(self, request, obj):
        """Redirige al flujo del panel si `obj` es eliminable; si no, avisa y devuelve `None`."""
        try:
            services.check_can_act(request.user, obj, choices.ACTION_ERASE)
        except services.AccountNotAllowed as exc:
            logger.warning('admin erase blocked code=%s actor_id=%s target_id=%s',
                           exc.code, request.user.pk, obj.pk)
            self.message_user(request, ERASE_BLOCKED_MESSAGES.get(exc.code, exc.message), messages.ERROR)
            return None
        return redirect(self._panel_erase_url(obj))

    def delete_view(self, request, object_id, extra_context=None):
        """No borra nunca: lleva a la pantalla de eliminación del panel (cliente o entrenador)."""
        if not self._is_owner(request.user) or IS_POPUP_VAR in request.GET:
            raise PermissionDenied   # solo la dueña; una ventana emergente no puede seguir el flujo
        obj = self.get_object(request, unquote(object_id))
        if obj is None:
            raise Http404
        response = self._redirect_to_safe_erase(request, obj)
        if response is None:
            return HttpResponseRedirect(reverse('admin:accounts_user_change', args=[obj.pk]))
        return response

    @admin.action(description='Eliminar (flujo seguro del panel)', permissions=['delete'])
    def erase_via_panel(self, request, queryset):
        """Con UNA cuenta elegida, abre su pantalla de eliminación del panel; con otra cantidad,
        avisa y no hace nada."""
        selected = list(queryset[:2])
        if len(selected) != 1:
            self.message_user(request, ERASE_ONE_AT_A_TIME_MESSAGE, messages.WARNING)
            return None
        return self._redirect_to_safe_erase(request, selected[0])


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
