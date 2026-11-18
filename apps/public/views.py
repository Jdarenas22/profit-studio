from django.shortcuts import render


def home(request):
    from .models import Testimonial
    testimonials = Testimonial.objects.filter(is_active=True).order_by('order', '-created_at')
    return render(request, 'public/home.html', {'testimonials': testimonials})


def about(request):
    from apps.accounts.models import User
    # Solo entrenadores activos (una cuenta desactivada o eliminada no se muestra)
    trainers = User.objects.filter(role='trainer', is_active=True, erased_at__isnull=True).order_by('date_joined')
    return render(request, 'public/about.html', {'trainers': trainers})


def contact(request):
    return render(request, 'public/contact.html')
