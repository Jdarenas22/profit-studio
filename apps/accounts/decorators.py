from functools import wraps
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
        except Exception:
            return render(request, 'accounts/no_membership.html')
        return view_func(request, *args, **kwargs)
    return _wrapped
