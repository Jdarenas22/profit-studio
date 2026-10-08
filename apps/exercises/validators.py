"""Validación de enlaces de video. Solo se aceptan enlaces HTTPS de YouTube.

Se usa en tres sitios, siempre con la misma regla:
  - el formulario del entrenador (`ExerciseForm`),
  - el campo del modelo (cubre también el admin de Django),
  - la propiedad `Exercise.youtube_embed_url`, que nunca devuelve una URL arbitraria.
"""
import re
from urllib.parse import parse_qs, urlparse

from django.core.exceptions import ValidationError

YOUTUBE_HOSTS = {
    'youtube.com', 'www.youtube.com', 'm.youtube.com',
    'youtube-nocookie.com', 'www.youtube-nocookie.com',
}
SHORT_HOSTS = {'youtu.be', 'www.youtu.be'}
VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')       # los IDs de YouTube miden 11 caracteres
UNSAFE_CHARS = re.compile(r'[\s\x00-\x1f\x7f\\]')    # espacios, control y barra invertida
MAX_URL_LENGTH = 200                                  # igual que models.URLField

INVALID_MESSAGE = ('Usa un enlace de YouTube que empiece por https:// '
                   '(youtube.com, youtu.be o youtube-nocookie.com).')


def extract_youtube_id(url):
    """Devuelve el ID de video si `url` es un enlace HTTPS válido de YouTube; si no, ''."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH or UNSAFE_CHARS.search(url):
        return ''
    try:
        parsed = urlparse(url)
        host = parsed.hostname          # ya sin usuario:clave@ y en minúsculas
        port = parsed.port
    except ValueError:
        return ''
    if parsed.scheme != 'https' or not host or port not in (None, 443):
        return ''

    candidate = ''
    if host in SHORT_HOSTS:
        candidate = parsed.path.lstrip('/').split('/')[0]
    elif host in YOUTUBE_HOSTS:
        parts = [p for p in parsed.path.split('/') if p]
        if parts == ['watch']:
            candidate = (parse_qs(parsed.query).get('v') or [''])[0]
        elif len(parts) == 2 and parts[0] in ('embed', 'shorts', 'live'):
            candidate = parts[1]
    return candidate if VIDEO_ID.match(candidate) else ''


def validate_youtube_url(value):
    if value and not extract_youtube_id(value):
        raise ValidationError(INVALID_MESSAGE, code='invalid_youtube_url')
