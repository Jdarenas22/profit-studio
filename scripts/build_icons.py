#!/usr/bin/env python
"""Genera el subconjunto propio de Font Awesome Free 6.5.0 (solo los íconos que usa el sitio).

Por qué: el CSS + las tipografías completas pesan ~295 KB por primera visita (sólida 157 KB, marcas 117 KB,
regular 25 KB, CSS 22 KB) y el sitio usa ~160 íconos. El subconjunto pesa unas decenas de KB, sale del propio
dominio (sin CDN ni SRI) y no cambia cómo se escriben las clases (`fa fa-user`, `fab fa-whatsapp`...).

Uso (fonttools y brotli NO son dependencias del proyecto: se instalan solo para regenerar):

    pip install fonttools brotli
    # descargar de https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/ : css/all.min.css,
    # webfonts/fa-solid-900.woff2 y webfonts/fa-brands-400.woff2 a una carpeta, p. ej. /tmp/fa
    python scripts/build_icons.py /tmp/fa

Escribe static/css/fontawesome-subset.css y static/webfonts/fa-{solid,brands}-subset.woff2. Después hay que
ejecutar las pruebas: `apps/public/tests/test_icons.py` falla si algún ícono usado no está en el subconjunto.

Licencia de Font Awesome Free: íconos CC BY 4.0, tipografías SIL OFL 1.1, código MIT (https://fontawesome.com/license/free).
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USED_RE = re.compile(r'\bfa-[a-z0-9]+(?:-[a-z0-9]+)*')
SEARCH = (
    [ROOT / 'templates', '*.html'], [ROOT / 'static' / 'js', '*.js'], [ROOT / 'static', 'offline.html'],
    [ROOT / 'apps', '*.py'],
)
# Clases `fa-*` que no son un ícono ni un modificador (familias y prefijos)
FAMILY_CLASSES = {'fa-solid', 'fa-brands', 'fa-regular', 'fa-classic', 'fa-sharp'}


def used_classes():
    found = set()
    for base, pattern in SEARCH:
        for path in Path(base).rglob(pattern) if Path(base).is_dir() else [Path(base) / pattern]:
            if not path.is_file() or 'tests' in path.parts or 'migrations' in path.parts:
                continue
            found.update(USED_RE.findall(path.read_text(encoding='utf-8', errors='ignore')))
    # `fa-solid-subset` / `fa-brands-subset` son nombres de ARCHIVO (la precarga de base.html), no íconos
    return {name for name in found - FAMILY_CLASSES if not name.endswith('-subset')}


def parse_css(css):
    """Devuelve (códigos por clase de ícono, reglas que no son de ícono por selector)."""
    codes, other = {}, []
    for selector, body in re.findall(r'((?:@[^{}]+)?[^{}@]+)\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}', css):
        selector = selector.strip()
        match = re.fullmatch(r'content:"\\([0-9a-f]+)"', body)
        if match and ':before' in selector:
            for part in selector.split(','):
                name = part.strip()
                if name.endswith(':before'):
                    codes[name[:-len(':before')].lstrip('.')] = int(match.group(1), 16)
        else:
            other.append((selector, body))
    return codes, other


def main(source):
    from fontTools import subset
    from fontTools.ttLib import TTFont

    source = Path(source)
    css = (source / 'all.min.css').read_text(encoding='utf-8')
    codes, other = parse_css(css)
    used = used_classes()

    # Modificadores utilizables (fa-spin, fa-2x, fa-fw...): reglas cuyo selector solo usa clases `fa-*` pedidas
    modifier_rules = []
    for selector, body in other:
        names = set(re.findall(r'\.(fa-[a-z0-9-]+)', selector))
        if selector.startswith('.fa-') and names and not any(':' in s for s in selector.split(',')[0:1]) and names & used:
            modifier_rules.append((selector, body))
    modifiers = {n for selector, _ in modifier_rules for n in re.findall(r'\.(fa-[a-z0-9-]+)', selector)}

    icons = sorted(name for name in used if name in codes)
    unknown = sorted(name for name in used if name not in codes and name not in modifiers
                     and not any(name in selector for selector, _ in other))
    if unknown:
        print('AVISO: no existen en Font Awesome Free 6.5.0 (hoy se ven vacíos en el sitio):', ', '.join(unknown))

    # ¿Qué familia tiene cada glifo?
    solid_font = TTFont(source / 'fa-solid-900.woff2')
    brands_font = TTFont(source / 'fa-brands-400.woff2')
    solid_cmap, brands_cmap = set(solid_font.getBestCmap()), set(brands_font.getBestCmap())
    solid_codes, brands_codes, ambiguous = set(), set(), []
    for name in icons:
        code = codes[name]
        if code in brands_cmap and code not in solid_cmap:
            brands_codes.add(code)
        elif code in solid_cmap and code not in brands_cmap:
            solid_codes.add(code)
        elif code in solid_cmap and code in brands_cmap:
            ambiguous.append(name)
            solid_codes.add(code)
            brands_codes.add(code)
    if ambiguous:
        print('Glifos presentes en ambas familias (se incluyen en las dos):', ', '.join(ambiguous))

    out_fonts = ROOT / 'static' / 'webfonts'
    out_fonts.mkdir(parents=True, exist_ok=True)
    for label, font_file, wanted in (('solid', 'fa-solid-900.woff2', solid_codes), ('brands', 'fa-brands-400.woff2', brands_codes)):
        options = subset.Options()
        options.flavor = 'woff2'
        options.layout_features = []
        options.name_IDs = ['*']
        options.notdef_outline = True
        options.hinting = False
        font = subset.load_font(str(source / font_file), options)
        subsetter = subset.Subsetter(options)
        subsetter.populate(unicodes=sorted(wanted))
        subsetter.subset(font)
        target = out_fonts / f'fa-{label}-subset.woff2'
        subset.save_font(font, str(target), options)
        print(f'{target.relative_to(ROOT)}: {target.stat().st_size} B ({len(wanted)} glifos)')

    header = ('/*! Font Awesome Free 6.5.0 (subconjunto) - https://fontawesome.com\n'
              ' * Licencia: https://fontawesome.com/license/free (íconos CC BY 4.0, tipografías SIL OFL 1.1, código MIT)\n'
              ' * GENERADO por scripts/build_icons.py: no editar a mano. Solo los íconos que usa el sitio. */\n')
    base = [
        '.fa,.fa-brands,.fa-solid,.fab,.fas{-moz-osx-font-smoothing:grayscale;-webkit-font-smoothing:antialiased;'
        'display:var(--fa-display,inline-block);font-style:normal;font-variant:normal;line-height:1;text-rendering:auto}',
        '.fa,.fa-solid,.fas{font-family:"Font Awesome 6 Free";font-weight:900}',
        '.fa-brands,.fab{font-family:"Font Awesome 6 Brands";font-weight:400}',
        '@font-face{font-family:"Font Awesome 6 Brands";font-style:normal;font-weight:400;font-display:block;'
        'src:url(../webfonts/fa-brands-subset.woff2) format("woff2")}',
        '@font-face{font-family:"Font Awesome 6 Free";font-style:normal;font-weight:900;font-display:block;'
        'src:url(../webfonts/fa-solid-subset.woff2) format("woff2")}',
    ]
    # Modificadores (y los @keyframes que necesiten)
    extra = [f'{selector}{{{body}}}' for selector, body in modifier_rules]
    if any('fa-spin' in selector or 'fa-pulse' in selector for selector, _ in modifier_rules):
        for selector, body in other:
            if selector in ('@keyframes fa-spin', '@-webkit-keyframes fa-spin'):
                extra.append(f'{selector}{{{body}}}')
        extra.append('@media (prefers-reduced-motion:reduce){.fa-spin{-webkit-animation-delay:-1ms;animation-delay:-1ms;'
                     '-webkit-animation-duration:1ms;animation-duration:1ms;-webkit-animation-iteration-count:1;'
                     'animation-iteration-count:1;transition-delay:0s;transition-duration:0s}}')
    glyphs = [f'.{name}:before{{content:"\\{codes[name]:x}"}}' for name in icons]
    target = ROOT / 'static' / 'css' / 'fontawesome-subset.css'
    target.write_text(header + '\n'.join(base + extra + glyphs) + '\n', encoding='utf-8')
    print(f'{target.relative_to(ROOT)}: {target.stat().st_size} B ({len(icons)} íconos, {len(modifier_rules)} modificadores)')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
