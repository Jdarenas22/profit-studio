from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ['username', 'email', 'first_name', 'last_name', 'role', 'is_active', 'date_joined']
    list_filter = ['role', 'is_active', 'date_joined']
    search_fields = ['username', 'email', 'first_name', 'last_name', 'phone']
    ordering = ['-date_joined']

    fieldsets = UserAdmin.fieldsets + (
        ('ProFit Studio', {
            'fields': ('role', 'phone', 'profile_photo'),
        }),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('ProFit Studio', {
            'fields': ('first_name', 'last_name', 'email', 'role', 'phone'),
        }),
    )
