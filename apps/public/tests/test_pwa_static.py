"""Archivos de la PWA: manifest, página sin conexión y service worker (solo revisión estática)."""
import json
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.accounts.tests.helpers import NO_MANIFEST

STATIC_DIR = Path(settings.BASE_DIR) / 'static'


class ManifestTests(SimpleTestCase):
    def setUp(self):
        self.manifest = json.loads((STATIC_DIR / 'manifest.json').read_text(encoding='utf-8'))

    def test_scope_and_start_url_are_inside_root(self):
        self.assertEqual(self.manifest['scope'], '/')
        self.assertTrue(self.manifest['start_url'].startswith('/'))

    def test_icons_exist_with_declared_size(self):
        from PIL import Image

        self.assertTrue(self.manifest['icons'])
        for icon in self.manifest['icons']:
            # Rutas relativas al manifest (también funcionan con nombres con huella).
            self.assertFalse(icon['src'].startswith('/'), icon['src'])
            path = STATIC_DIR / icon['src']
            self.assertTrue(path.exists(), icon['src'])
            width, height = (int(n) for n in icon['sizes'].split('x'))
            with Image.open(path) as img:
                self.assertEqual(img.size, (width, height), icon['src'])


class OfflinePageTests(SimpleTestCase):
    def test_offline_page_has_no_external_resources(self):
        html = (STATIC_DIR / 'offline.html').read_text(encoding='utf-8')
        self.assertIn('lang="es"', html)
        self.assertNotRegex(html, r'(src|href)=["\']https?://')
        self.assertNotIn('<link rel="stylesheet"', html)


class ServiceWorkerSourceTests(SimpleTestCase):
    def setUp(self):
        self.sw = (STATIC_DIR / 'sw.js').read_text(encoding='utf-8')

    def test_precaches_only_offline_page(self):
        self.assertIn("OFFLINE_URL = '/static/offline.html'", self.sw)
        self.assertNotIn("PRECACHE = [", self.sw)

    def test_never_caches_private_responses_or_html(self):
        self.assertIn('no-store|private', self.sw)
        self.assertIn("startsWith('text/html')", self.sw)
        for prefix in ('media', 'payments', 'health', 'plans', 'accounts', 'api'):
            self.assertRegex(self.sw, rf'NEVER_CACHE = .*\b{prefix}\b')

    def test_old_caches_are_removed(self):
        self.assertNotIn("'profit-v1'", self.sw)
        self.assertIn('caches.delete', self.sw)


@NO_MANIFEST
class BaseTemplatePwaTests(TestCase):
    def test_home_links_manifest_with_static_and_registers_root_worker(self):
        html = self.client.get(reverse('home')).content.decode()
        self.assertRegex(html, r'<link rel="manifest" href="[^"]*manifest[^"]*\.json"')
        self.assertIn("register('/sw.js', { scope: '/' })", html)
        self.assertNotIn("/static/sw.js", html)

    def test_home_images_have_dimensions_and_alt(self):
        html = self.client.get(reverse('home')).content.decode()
        for tag in re.findall(r'<img\b[^>]*>', html, re.S):
            self.assertRegex(tag, r'\balt=', tag)
            self.assertRegex(tag, r'\bwidth="\d+"', tag)
            self.assertRegex(tag, r'\bheight="\d+"', tag)
