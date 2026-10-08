"""IP real del cliente para django-axes detrás del proxy de Railway.

django-axes 6.5.0 solo entiende las opciones de proxy (AXES_IPWARE_*) si está
instalado el paquete opcional django-ipware; sin él las ignora en silencio y
usa REMOTE_ADDR (la IP del proxy, igual para todos los visitantes). Esta función
evita añadir esa dependencia: se activa con AXES_CLIENT_IP_CALLABLE.

Railway envía la IP del cliente en la cabecera X-Real-IP. Solo debe usarse
detrás del proxy de Railway (en production.py); en local no hay proxy.
"""
import ipaddress


def get_client_ip(request):
    candidate = request.META.get('HTTP_X_REAL_IP', '').strip()
    if candidate:
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            pass  # cabecera mal formada: se ignora y se usa REMOTE_ADDR
    return request.META.get('REMOTE_ADDR')
