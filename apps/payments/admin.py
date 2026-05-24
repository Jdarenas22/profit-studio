from django.contrib import admin
from .models import Payment, ManualPayment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['reference', 'user', 'plan', 'amount_display', 'status', 'method_display', 'created_at']
    list_filter = ['status', 'payment_method_type', 'currency']
    search_fields = ['reference', 'user__username', 'user__email', 'wompi_transaction_id']
    readonly_fields = ['reference', 'wompi_transaction_id', 'raw_webhook', 'created_at', 'updated_at']
    ordering = ['-created_at']

    def amount_display(self, obj):
        return f"${obj.amount_cop:,.0f} COP"
    amount_display.short_description = 'Monto'

    def method_display(self, obj):
        return obj.method_display
    method_display.short_description = 'Método'


@admin.register(ManualPayment)
class ManualPaymentAdmin(admin.ModelAdmin):
    list_display = ['user', 'amount', 'method', 'payment_date', 'plan', 'trainer']
    list_filter = ['method', 'payment_date']
    search_fields = ['user__username', 'user__first_name', 'user__last_name']
    raw_id_fields = ['user', 'trainer', 'plan']
    readonly_fields = ['created_at']
    date_hierarchy = 'payment_date'
