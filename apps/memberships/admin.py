from django.contrib import admin
from .models import MembershipPlan, Membership


@admin.register(MembershipPlan)
class MembershipPlanAdmin(admin.ModelAdmin):
    list_display = ['name', 'duration_days', 'reference_price', 'is_active']
    list_filter = ['is_active']
    list_editable = ['is_active']


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = [
        'user', 'plan', 'start_date', 'end_date',
        'is_active', 'status_display', 'days_remaining',
    ]
    list_filter = ['is_active', 'plan']
    search_fields = ['user__username', 'user__email', 'user__first_name', 'user__last_name']
    readonly_fields = ['status_display', 'days_remaining', 'created_at', 'updated_at']
    raw_id_fields = ['user', 'activated_by']
