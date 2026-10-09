"""Admin de Django: SOLO los textos de consentimiento y SOLO para la superusuaria.

Los registros de consentimiento (`ConsentRecord`) y los demás datos de salud no se registran
aquí a propósito: el admin se salta la regla de asignación de clientes. La superusuaria ve el
historial por la web (fase A2).
"""
from django.contrib import admin, messages

from .models import ConsentTextVersion


@admin.register(ConsentTextVersion)
class ConsentTextVersionAdmin(admin.ModelAdmin):
    list_display = ['purpose', 'version', 'is_current', 'published_at', 'pending_markers']
    list_filter = ['purpose', 'is_current']
    ordering = ['purpose', '-version']

    @admin.display(description='Marcadores sin reemplazar')
    def pending_markers(self, obj):
        return ', '.join(obj.placeholders) or '—'

    # Solo la superusuaria (el rol de entrenador/a común no basta, aunque sea del staff)
    def _is_boss(self, request):
        user = request.user
        return bool(user.is_active and user.is_superuser and getattr(user, 'is_trainer', False))

    def has_module_permission(self, request):
        return self._is_boss(request)

    def has_view_permission(self, request, obj=None):
        return self._is_boss(request)

    def has_add_permission(self, request):
        return self._is_boss(request)

    def has_change_permission(self, request, obj=None):
        return self._is_boss(request)

    def has_delete_permission(self, request, obj=None):
        return False   # es la evidencia de lo que se aceptó

    def get_readonly_fields(self, request, obj=None):
        # Una versión publicada no se edita (solo se puede dejar de marcar como vigente)
        if obj is not None:
            return ['purpose', 'version', 'body', 'published_at']
        return []

    def get_changeform_initial_data(self, request):
        # Sugiere el número de la versión siguiente
        data = super().get_changeform_initial_data(request)
        purpose = request.GET.get('purpose')
        if purpose:
            last = ConsentTextVersion.objects.filter(purpose=purpose).order_by('-version').first()
            data['version'] = (last.version + 1) if last else 1
        return data

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.has_placeholders:
            messages.warning(
                request,
                'Este texto todavía tiene marcadores sin reemplazar: ' + ', '.join(obj.placeholders)
                + '. No lo publiques en producción hasta que un abogado lo apruebe.',
            )
