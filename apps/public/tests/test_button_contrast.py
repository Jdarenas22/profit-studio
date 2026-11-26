"""Rellenos claros (naranja de marca y verde) llevan texto oscuro: el blanco sobre #FF6B00 da solo 2,86:1."""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

TEMPLATES = Path(settings.BASE_DIR) / 'templates'
DELIM = re.compile(r'(\{%|%\}|\{\{|\}\}|"|\'|<|>)')
ICON = re.compile(r'<i\s[^>]*>')
# Relleno en reposo que no admite texto blanco (el 700 del verde y el zinc sí lo admiten)
LIGHT_FILL = re.compile(r'bg-(?:primary(?:-dark)?|green-(?:400|500|600))')
HOVER_LIGHT_FILL = re.compile(r'(?:hover|focus|group-hover):bg-(?:primary(?:-dark)?|green-(?:400|500|600))')
WHITE = re.compile(r'text-white(?:/\d+)?')
HOVER_WHITE = re.compile(r'(?:hover|focus|group-hover):text-white(?:/\d+)?')
WEAK_ORANGE_TEXT = re.compile(r'(?<![\w/-])text-primary/(?:10|20|25|30|40|50|60|70)\b')


def segments(line):
    for part in DELIM.split(line):
        if part.strip() and not DELIM.fullmatch(part):
            yield part.split()


class ButtonContrastGuardTests(SimpleTestCase):
    def offenders(self, predicate):
        found = []
        for path in sorted(TEMPLATES.rglob('*.html')):
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
                if predicate(ICON.sub('', line)):
                    found.append(f'{path.relative_to(TEMPLATES)}:{number}')
        return found

    def test_no_white_text_on_orange_or_light_green_fill(self):
        def bad(line):
            for toks in segments(line):
                fill = any(LIGHT_FILL.fullmatch(t) for t in toks)
                hover = any(HOVER_LIGHT_FILL.fullmatch(t) for t in toks)
                if fill and any(WHITE.fullmatch(t) or HOVER_WHITE.fullmatch(t) for t in toks):
                    return True
                if hover and any(HOVER_WHITE.fullmatch(t) for t in toks):
                    return True
            return False
        found = self.offenders(bad)
        self.assertEqual(found, [], 'Texto blanco sobre relleno naranja o verde claro (usa text-black): ' + ', '.join(found[:10]))

    def test_no_faded_orange_text(self):
        found = self.offenders(lambda line: bool(WEAK_ORANGE_TEXT.search(line)))
        self.assertEqual(found, [], 'Texto naranja tenue (usa text-primary): ' + ', '.join(found[:10]))


class GuardDetectsTheBadCasesTests(SimpleTestCase):
    """La propia guardia debe fallar con el patrón antiguo y pasar con el nuevo."""

    def flags(self, line):
        for toks in segments(ICON.sub('', line)):
            fill = any(LIGHT_FILL.fullmatch(t) for t in toks)
            hover = any(HOVER_LIGHT_FILL.fullmatch(t) for t in toks)
            if fill and any(WHITE.fullmatch(t) or HOVER_WHITE.fullmatch(t) for t in toks):
                return True
            if hover and any(HOVER_WHITE.fullmatch(t) for t in toks):
                return True
        return False

    def test_old_patterns_are_flagged(self):
        for line in ('<a class="bg-primary hover:bg-primary-dark text-white font-bold">',
                     '<a class="{% if x %}bg-primary text-white{% else %}text-white/50{% endif %}">',
                     '<span class="bg-green-500 hover:bg-green-600 text-white">',
                     '<a class="bg-primary/10 hover:bg-primary text-primary hover:text-white">'):
            with self.subTest(line=line):
                self.assertTrue(self.flags(line))

    def test_new_and_dark_patterns_are_fine(self):
        for line in ('<a class="bg-primary hover:bg-primary-dark text-black font-bold">',
                     '<a class="bg-zinc-800 hover:bg-zinc-700 text-white">',
                     '<a class="bg-green-700 text-white">',
                     '<a class="bg-black hover:bg-zinc-900 text-white">',
                     '<i class="fa fa-x bg-primary text-white"></i>'):
            with self.subTest(line=line):
                self.assertFalse(self.flags(line))
