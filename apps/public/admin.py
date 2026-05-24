from django.contrib import admin
from .models import Testimonial


@admin.register(Testimonial)
class TestimonialAdmin(admin.ModelAdmin):
    list_display = ['name', 'role', 'rating', 'is_active', 'order', 'created_at']
    list_filter = ['is_active', 'rating']
    list_editable = ['is_active', 'order']
    search_fields = ['name', 'text']
    fields = ['name', 'role', 'text', 'photo', 'rating', 'is_active', 'order']
