from functools import wraps
from django.core.exceptions import ObjectDoesNotExist
from django.contrib import messages
from django.shortcuts import redirect, render


def trainer_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not request.user.is_trainer:
            return render(request, 'accounts/forbidden.html', status=403)
        return view_func(request, *args, **kwargs)
    return _wrapped


def superuser_required(view_func):
    """Solo la entrenadora principal (trainer + is_superuser).

    Anónimo -> login. Entrenador sin permiso -> aviso y vuelta a su panel.
    Cliente -> 403.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not request.user.is_trainer:
            return render(request, 'accounts/forbidden.html', status=403)
        if not request.user.is_superuser:
            messages.error(request, 'No tienes permiso para acceder a esta sección.')
            return redirect('trainer_dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped


def membership_required(view_func):
    """Permite acceso a trainers siempre. Para members, exige membresía activa."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if request.user.is_trainer:
            return view_func(request, *args, **kwargs)
        try:
            if not request.user.membership.is_valid:
                return render(request, 'accounts/membership_expired.html', {
                    'membership': request.user.membership,
                })
        except ObjectDoesNotExist:
            return render(request, 'accounts/no_membership.html')
        return view_func(request, *args, **kwargs)
    return _wrapped
