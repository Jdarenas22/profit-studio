"""Archivos estáticos con huella (CompressedManifestStaticFilesStorage, producción).

Con el manifest, `{% static 'x' %}` de un archivo que NO existe lanza ValueError y la página responde
500 (antes, con CompressedStaticFilesStorage, solo habría fallado esa imagen con un 404). Estas pruebas
lo detectan antes de desplegar:

1. Todo `{% static '...' %}` de las plantillas apunta a un archivo que existe (los mismos buscadores
   que usa `collectstatic`).
2. Todo `/static/...` escrito a mano (plantillas y JS) también existe.
3. `collectstatic` con el almacenamiento de producción termina sin errores (su post-proceso falla si
   un CSS tiene `url(...)` a un archivo inexistente) y deja un manifest con huella.
"""
import re
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

BASE_DIR = Path(settings.BASE_DIR)

# {% static 'ruta' %} y {% static "ruta" as nombre %}. Las rutas dinámicas ({% static variable %}) no se
# pueden comprobar sin renderizar y se omiten.
STATIC_TAG = re.compile(r'''\{%-?\s*static\s+(?P<q>['"])(?P<path>[^'"]+)(?P=q)''')
# /static/algo.ext escrito a mano (sin etiqueta). Solo rutas con extensión de archivo.
HAND_WRITTEN = re.compile(r'''(?<![\w.:/-])/static/(?P<path>[\w./-]+\.[A-Za-z0-9]+)''')
NEWLINE = chr(10)


def template_files():
    yield from (BASE_DIR / 'templates').rglob('*.html')
    yield from (BASE_DIR / 'apps').glob('*/templates/**/*.html')


def line_of(text, offset):
    return text.count(NEWLINE, 0, offset) + 1


def label(path):
    try:
        return Path(path).relative_to(BASE_DIR).as_posix()
    except ValueError:
        return Path(path).name


def missing_static_tags(paths):
    """(total de etiquetas, lista de «archivo:línea -> {% static 'x' %}» cuyo archivo no existe)."""
    missing, total = [], 0
    for path in paths:
        text = Path(path).read_text(encoding='utf-8')
        for match in STATIC_TAG.finditer(text):
            total += 1
            if '{{' in match['path'] or '{%' in match['path']:
                continue
            if not finders.find(match['path']):
                missing.append(f"{label(path)}:{line_of(text, match.start())} -> {{% static '{match['path']}' %}}")
    return total, missing


def missing_hand_written(paths):
    missing = []
    for path in paths:
        text = Path(path).read_text(encoding='utf-8')
        for match in HAND_WRITTEN.finditer(text):
            if not finders.find(match['path']):
                missing.append(f"{label(path)}:{line_of(text, match.start())} -> /static/{match['path']}")
    return missing


class StaticReferencesExistTests(SimpleTestCase):
    def test_templates_use_the_static_tag_for_files_that_exist(self):
        total, missing = missing_static_tags(template_files())
        self.assertGreater(total, 0, 'no se encontró ninguna etiqueta {% static %}: ¿cambió el patrón?')
        self.assertEqual(missing, [], 'Archivos estáticos que no existen (en producción darían error 500):'
                         + NEWLINE + NEWLINE.join(missing))

    def test_hand_written_static_urls_point_to_files_that_exist(self):
        sources = [*template_files(), *(BASE_DIR / 'static').rglob('*.js'), *(BASE_DIR / 'static').rglob('*.html')]
        missing = missing_hand_written(sources)
        self.assertEqual(missing, [], 'Rutas /static/ escritas a mano que no existen:'
                         + NEWLINE + NEWLINE.join(missing))

    def test_the_scanner_detects_a_reference_to_a_missing_file(self):
        """Contraprueba: con una plantilla mal escrita, el recorrido SÍ falla (y con una buena, no)."""
        with tempfile.TemporaryDirectory() as folder:
            bad = Path(folder) / 'mala.html'
            bad.write_text(NEWLINE.join([
                '<p>ok</p>',
                '<img src="{% static \'images/no-existe-nunca.png\' %}">',
                "<script>navigator.serviceWorker.register('/static/tampoco-existe.js')</script>",
            ]), encoding='utf-8')
            good = Path(folder) / 'buena.html'
            good.write_text('<link href="{% static \'css/app.css\' %}"> {% static variable %}', encoding='utf-8')
            total, missing = missing_static_tags([bad, good])
            self.assertEqual(total, 2)
            self.assertEqual(len(missing), 1)
            self.assertIn("mala.html:2 -> {% static 'images/no-existe-nunca.png' %}", missing[0])
            hand = missing_hand_written([bad, good])
            self.assertEqual(len(hand), 1)
            self.assertIn('mala.html:3 -> /static/tampoco-existe.js', hand[0])

    def test_patterns(self):
        self.assertTrue(STATIC_TAG.search('<link href="{% static \'css/app.css\' %}">'))
        self.assertTrue(STATIC_TAG.search('{% static "images/x.png" as logo %}'))
        self.assertIsNone(STATIC_TAG.search('{% static variable %}'))
        self.assertEqual(HAND_WRITTEN.search('x="/static/offline.html"')['path'], 'offline.html')
        self.assertIsNone(HAND_WRITTEN.search('https://cdn.ejemplo.com/static/x.js'))     # otro sitio
        self.assertTrue(finders.find('css/app.css'))
        self.assertFalse(finders.find('images/no-existe-nunca.png'))


class CollectstaticWithManifestTests(SimpleTestCase):
    """Lo que corre Railway en el build: debe terminar bien y dejar el manifest."""

    def test_collectstatic_with_the_production_storage_succeeds(self):
        storages = {**settings.STORAGES,
                    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'}}
        with tempfile.TemporaryDirectory() as root, override_settings(STATIC_ROOT=root, STORAGES=storages):
            call_command('collectstatic', interactive=False, verbosity=0)
            self.assertTrue((Path(root) / 'staticfiles.json').exists())
            from django.contrib.staticfiles.storage import staticfiles_storage
            hashed = staticfiles_storage.url('css/app.css')
            self.assertRegex(hashed, r'^/static/css/app\.[0-9a-f]{12}\.css$')
            self.assertTrue((Path(root) / hashed.removeprefix('/static/')).exists())
            self.assertTrue((Path(root) / 'css' / 'app.css').exists())      # el nombre sin huella sigue existiendo
            with self.assertRaises(ValueError):                              # lo que pasaría con una plantilla mal escrita
                staticfiles_storage.url('images/no-existe-nunca.png')

    def test_production_settings_use_the_manifest_storage(self):
        # El valor real de producción lo comprueba test_deploy_config (subproceso); aquí, el texto
        source = (BASE_DIR / 'config' / 'settings' / 'production.py').read_text(encoding='utf-8')
        self.assertIn("'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'}", source)
        self.assertNotIn("'whitenoise.storage.CompressedStaticFilesStorage'", source)
