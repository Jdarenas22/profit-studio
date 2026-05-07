from django.shortcuts import render
from django.http import JsonResponse


def home(request):
    return render(request, 'public/home.html')


def about(request):
    return render(request, 'public/about.html')


def contact(request):
    return render(request, 'public/contact.html')


def health_check(request):
    return JsonResponse({'status': 'ok'})
