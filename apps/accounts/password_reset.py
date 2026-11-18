"""Recuperación con límites por IP y cuenta, sin revelar si existe el correo.

La caché debe ser compartida (Redis) para un límite global entre workers. Si falla,
no se envía correo; la respuesta pública sigue siendo la confirmación genérica.
"""
import logging

from django.conf import settings
from django.contrib.auth.views import PasswordResetView
from django.core.cache import cache
from django.http import HttpResponseRedirect
from django.utils.crypto import salted_hmac
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)
WINDOW_SECONDS = 15 * 60
ACCOUNT_LIMIT = 2
IP_LIMIT = 10


def _allow(scope, value, limit):
    digest = salted_hmac('profit.password-reset.' + scope, value, algorithm='sha256').hexdigest()
    key = 'password-reset:' + scope + ':' + digest
    try:
        if cache.add(key, 1, timeout=WINDOW_SECONDS):
            return True
        try:
            return cache.incr(key) <= limit
        except ValueError:
            # La clave pudo vencer entre add e incr. Se intenta reservar una sola vez.
            return bool(cache.add(key, 1, timeout=WINDOW_SECONDS))
    except Exception:
        logger.warning('password reset: cache unavailable; email suppressed')
        return False


class RateLimitedPasswordResetView(PasswordResetView):
    def form_valid(self, form):
        # Usa únicamente la regla de IP configurada para el proxy; en local, REMOTE_ADDR.
        resolver = getattr(settings, 'AXES_CLIENT_IP_CALLABLE', None)
        if isinstance(resolver, str):
            resolver = import_string(resolver)
        ip = resolver(self.request) if callable(resolver) else self.request.META.get('REMOTE_ADDR')
        address = form.cleaned_data['email'].strip().casefold()
        if not _allow('ip', str(ip or 'unknown'), IP_LIMIT):
            return HttpResponseRedirect(self.get_success_url())
        if not _allow('account', address, ACCOUNT_LIMIT):
            return HttpResponseRedirect(self.get_success_url())
        return super().form_valid(form)
