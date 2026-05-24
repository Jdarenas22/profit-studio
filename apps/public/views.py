from django.shortcuts import render
from django.http import JsonResponse


def home(request):
    return render(request, 'public/home.html')


def about(request):
    from apps.accounts.models import User
    trainers = User.objects.filter(role='trainer').order_by('date_joined')
    return render(request, 'public/about.html', {'trainers': trainers})


def contact(request):
    return render(request, 'public/contact.html')


def health_check(request):
    return JsonResponse({'status': 'ok'})
