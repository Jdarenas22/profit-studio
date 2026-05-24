from django.shortcuts import render
from django.http import JsonResponse


def home(request):
    from .models import Testimonial
    testimonials = Testimonial.objects.filter(is_active=True).order_by('order', '-created_at')
    return render(request, 'public/home.html', {'testimonials': testimonials})


def about(request):
    from apps.accounts.models import User
    trainers = User.objects.filter(role='trainer').order_by('date_joined')
    return render(request, 'public/about.html', {'trainers': trainers})


def contact(request):
    return render(request, 'public/contact.html')


def health_check(request):
    return JsonResponse({'status': 'ok'})
