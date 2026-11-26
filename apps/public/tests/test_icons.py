"""Subconjunto propio de Font Awesome: debe cubrir TODOS los íconos que usa el sitio."""
import importlib.util
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)
CSS = ROOT / 'static' / 'css' / 'fontawesome-subset.css'


def load_builder():
    spec = importlib.util.spec_from_file_location('build_icons', ROOT / 'scripts' / 'build_icons.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)       # fonttools solo se importa dentro de main(): no hace falta para probar
    return module


class IconSubsetTests(SimpleTestCase):
    def test_every_icon_used_by_the_site_is_in_the_subset(self):
        css = CSS.read_text(encoding='utf-8')
        defined = set(re.findall(r'\.(fa-[a-z0-9-]+):before', css))
        modifiers = set(re.findall(r'\.(fa-[a-z0-9-]+)(?=[,{])', css))
        missing = sorted(name for name in load_builder().used_classes() if name not in defined | modifiers)
        self.assertEqual(missing, [], 'Íconos usados que no están en el subconjunto (o no existen en Font Awesome Free). '
                                      'Si es uno nuevo y existe, regenera con scripts/build_icons.py: ' + ', '.join(missing))

    def test_fonts_exist_and_are_small(self):
        for name, limit in (('fa-solid-subset.woff2', 60_000), ('fa-brands-subset.woff2', 20_000)):
            path = ROOT / 'static' / 'webfonts' / name
            self.assertTrue(path.is_file(), name)
            self.assertLess(path.stat().st_size, limit, f'{name} dejó de ser un subconjunto')
            self.assertEqual(path.read_bytes()[:4], b'wOF2')

    def test_css_points_to_the_local_fonts_and_keeps_the_license_notice(self):
        css = CSS.read_text(encoding='utf-8')
        self.assertIn('fa-solid-subset.woff2', css)
        self.assertIn('fa-brands-subset.woff2', css)
        self.assertIn('SIL OFL 1.1', css)            # atribución de la licencia de Font Awesome Free
        self.assertNotIn('http', css.split('*/', 1)[1])   # sin recursos externos

    def test_base_template_uses_the_local_subset_not_the_cdn(self):
        base = (ROOT / 'templates' / 'base.html').read_text(encoding='utf-8')
        self.assertIn("css/fontawesome-subset.css", base)
        self.assertIn("webfonts/fa-solid-subset.woff2", base)           # precarga: los íconos aparecen antes
        self.assertNotIn('font-awesome/6.5.0', base)
        self.assertNotRegex(base, r'cdnjs\.cloudflare\.com[^"\']*font-awesome')

    def test_the_site_no_longer_uses_pro_only_icons(self):
        self.assertNotIn('fa-shield-check', ' '.join(load_builder().used_classes()))
