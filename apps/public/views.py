import hashlib
import logging
from pathlib import Path

from django.contrib.staticfiles import finders
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils.cache import get_conditional_response
from django.views.decorators.http import require_safe

logger = logging.getLogger(__name__)

# Archivo fuente del service worker, relativo a STATICFILES_DIRS (static/sw.js en el repositorio).
SERVICE_WORKER_NAME = 'sw.js'


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


@require_safe
def service_worker(request):
    """Entrega el service worker de la PWA en `/sw.js` (la raíz).

    Un service worker solo controla las páginas que están dentro de su propia ruta. Servido desde
    `/static/sw.js` el navegador rechaza registrarlo con `scope: '/'` (hace falta la cabecera
    `Service-Worker-Allowed`, que WhiteNoise no envía), y la PWA no funciona sin avisar. Servido desde
    la raíz, controla todo el sitio.

    - Lee el archivo FUENTE del repositorio (`finders`, o sea STATICFILES_DIRS), no la copia con huella
      de STATIC_ROOT: el service worker no puede tener un nombre distinto en cada despliegue, porque el
      navegador lo busca siempre en la misma URL para ver si cambió.
    - Se lee en cada petición, no una vez por proceso. Pesa pocos KB y el sistema operativo lo tiene
      en memoria; a cambio, el desarrollo (runserver) y las pruebas ven los cambios al instante y no
      hay estado en el proceso (12-Factor). Un nuevo despliegue cambia los bytes y por tanto el ETag.
    - `no-cache` obliga al navegador a preguntar siempre si cambió (con ETag la respuesta es un 304 sin
      cuerpo): una versión vieja del worker nunca se queda pegada en la caché HTTP.
    - No toca la sesión ni la base de datos, y no pide inicio de sesión (el navegador lo pide sin
      cookies al instalar la PWA).
    """
    path = finders.find(SERVICE_WORKER_NAME)
    if isinstance(path, (list, tuple)):          # defensivo: find() devuelve un str salvo con find_all=True
        path = path[0] if path else None
    if not path:
        logger.warning('Service worker: no se encontró %s en los archivos estáticos.', SERVICE_WORKER_NAME)
        raise Http404
    try:
        body = Path(path).read_bytes()
    except OSError:
        logger.warning('Service worker: no se pudo leer %s.', SERVICE_WORKER_NAME)
        raise Http404 from None

    etag = '"%s"' % hashlib.sha256(body).hexdigest()[:32]
    response = get_conditional_response(request, etag=etag)    # 304 si el navegador ya tiene esta versión
    if response is None:
        response = HttpResponse(body, content_type='application/javascript; charset=utf-8')
    response['ETag'] = etag
    response['Cache-Control'] = 'no-cache'
    response['Service-Worker-Allowed'] = '/'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
