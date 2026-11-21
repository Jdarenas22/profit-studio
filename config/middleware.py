"""Middleware propios del proyecto."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def format_permissions_policy(policy):
    """Convierte `{'camera': [], 'payment': ['self', 'https://x.co']}` en el valor de la cabecera:
    `camera=(), payment=(self "https://x.co")`. `self` y `*` van sin comillas; los orígenes, entre comillas."""
    parts = []
    for feature, allowlist in policy.items():
        if isinstance(allowlist, str) or not feature or not feature.replace('-', '').isalnum():
            raise ImproperlyConfigured(
                f'PERMISSIONS_POLICY: «{feature}» debe ser un nombre de función y su valor, una lista de orígenes.')
        items = ' '.join(item if item in ('self', '*') else f'"{item}"' for item in allowlist)
        parts.append(f'{feature}=({items})' if items != '*' else f'{feature}=*')
    return ', '.join(parts)


class PermissionsPolicyMiddleware:
    """Añade `Permissions-Policy` a todas las respuestas: le dice al navegador qué funciones del
    dispositivo (cámara, micrófono, ubicación...) NO puede usar esta web ni ninguna página incrustada
    en ella (un iframe, un script de un tercero). Si algún día un script malicioso se cuela, no podrá
    pedir la cámara ni la ubicación de la persona.

    La política sale de `settings.PERMISSIONS_POLICY`. Una vista que ponga su propia cabecera
    `Permissions-Policy` la conserva (no se pisa). Va justo después de SecurityMiddleware y antes de
    WhiteNoise, para que también la lleven los archivos estáticos.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.value = format_permissions_policy(getattr(settings, 'PERMISSIONS_POLICY', {}))

    def __call__(self, request):
        response = self.get_response(request)
        if self.value and 'Permissions-Policy' not in response:
            response['Permissions-Policy'] = self.value
        return response
