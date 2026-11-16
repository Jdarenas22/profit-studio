"""El CSS de Tailwind se compila en local (scripts/build-css.*) y se guarda en git.

Estas pruebas evitan volver al Play CDN y detectan un static/css/app.css desactualizado
(una plantilla con clases nuevas y sin recompilar se vería rota en producción).
"""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, override_settings

BASE_DIR = Path(settings.BASE_DIR)
BASE_TEMPLATE = BASE_DIR / 'templates' / 'base.html'
APP_CSS = BASE_DIR / 'static' / 'css' / 'app.css'
BUILD_SH = BASE_DIR / 'scripts' / 'build-css.sh'
BUILD_PS1 = BASE_DIR / 'scripts' / 'build-css.ps1'

# En pruebas no hay collectstatic ni manifest (ver apps/accounts/tests/helpers.py).
NO_MANIFEST = override_settings(
    STORAGES={
        **settings.STORAGES,
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)


def tailwind_version():
    match = re.search(r'^TAILWIND_VERSION="([^"]+)"', BUILD_SH.read_text(encoding='utf-8'), re.M)
    return match.group(1) if match else None


def normalized(text):
    # git (core.autocrlf) puede cambiar los finales de línea al hacer checkout en Windows.
    return text.replace('\r\n', '\n').strip()


class TailwindBuildTests(SimpleTestCase):
    def test_base_template_does_not_use_play_cdn(self):
        source = BASE_TEMPLATE.read_text(encoding='utf-8')
        self.assertNotIn('cdn.tailwindcss.com', source)
        self.assertNotIn('tailwind.config', source)
        self.assertIn("{% static 'css/app.css' %}", source)

    def test_no_template_uses_play_cdn(self):
        offenders = [
            str(path.relative_to(BASE_DIR))
            for path in (BASE_DIR / 'templates').rglob('*.html')
            if 'cdn.tailwindcss.com' in path.read_text(encoding='utf-8')
        ]
        self.assertEqual(offenders, [])

    def test_compiled_css_exists(self):
        self.assertTrue(APP_CSS.is_file(), 'Falta static/css/app.css: ejecuta scripts/build-css.ps1 o .sh')
        css = APP_CSS.read_text(encoding='utf-8')
        self.assertIn('tailwindcss v3.4', css)
        # Color de marca del tema (tailwind.config.js)
        self.assertIn('.bg-primary{', css)

    def test_build_scripts_pin_the_same_version(self):
        version = tailwind_version()
        self.assertRegex(version or '', r'^3\.4\.\d+$')
        self.assertIn(f"$TailwindVersion = '{version}'", BUILD_PS1.read_text(encoding='utf-8'))

    @NO_MANIFEST
    def test_rendered_page_links_compiled_css(self):
        response = self.client.get('/accounts/login/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/static/css/app.css"')
        self.assertNotContains(response, 'cdn.tailwindcss.com')

    def test_compiled_css_is_up_to_date(self):
        npx = shutil.which('npx')
        if npx is None:
            self.skipTest('npx no está disponible (Railway/CI no tienen Node)')
        version = tailwind_version()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'app.css'
            result = subprocess.run(
                [npx, '--yes', f'tailwindcss@{version}',
                 '-c', 'tailwind.config.js', '-i', 'frontend/tailwind.css',
                 '-o', str(out), '--minify'],
                cwd=BASE_DIR, capture_output=True, text=True, timeout=300,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            fresh = out.read_text(encoding='utf-8')
        self.assertEqual(
            normalized(fresh), normalized(APP_CSS.read_text(encoding='utf-8')),
            'static/css/app.css está desactualizado: ejecuta scripts/build-css.ps1 o .sh',
        )
