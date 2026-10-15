"""Arma `informe.html` y `informe.pdf` a partir de `cuerpo.html` y de los anexos.

Uso (desde la raíz del proyecto):
    .venv/Scripts/python.exe docs/actividad-patrones/construir_informe.py

No necesita dependencias: genera el HTML con la biblioteca estándar y usa Chrome en modo sin
interfaz para producir el PDF. El texto del informe se edita en `cuerpo.html`.
"""
import html
import re
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent.parent
ANEXOS = AQUI / 'anexos'
EXP = ANEXOS / 'experimento'
CHROME_RUTAS = [
    Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe'),
    Path(r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe'),
    Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'),
]

TITULO = 'Patrones de diseño en el código generado con IA'
SUBTITULO = 'Informe de experimento sobre el sistema ProFit Studio (apps plans y health)'

CSS = """
@page { size: A4; margin: 2.5cm 2.5cm 2.5cm 3cm;
        @bottom-center { content: counter(page); font-family: "Times New Roman", serif; font-size: 10pt; } }
@page portada { @bottom-center { content: none; } }
html { font-family: "Times New Roman", Times, serif; font-size: 12pt; line-height: 1.5; color: #000; }
body { margin: 0; }
h1 { font-size: 14pt; text-align: center; margin: 22pt 0 8pt; break-after: avoid; }
h2 { font-size: 12pt; text-align: left; margin: 16pt 0 4pt; break-after: avoid; }
h3 { font-size: 12pt; font-style: italic; margin: 12pt 0 4pt; break-after: avoid; }
p { margin: 0 0 8pt; }
ul, ol { margin: 0 0 8pt; padding-left: 22pt; }
li { margin-bottom: 3pt; }
code { font-family: Consolas, "Courier New", monospace; font-size: 10pt; }
.pend { background: #fff3a8; padding: 0 2pt; }
.ref { padding-left: 28pt; text-indent: -28pt; }
.portada { page: portada; text-align: center; padding-top: 5cm; break-after: page; }
.portada h1 { font-size: 20pt; margin-bottom: 14pt; }
.portada .sub { font-size: 14pt; margin-bottom: 60pt; }
.portada p { margin: 0 0 10pt; }
.toc h1 { margin-top: 0; }
.toc { break-after: page; }
.toc ul { list-style: none; padding-left: 0; }
.toc li.n2 { padding-left: 18pt; font-size: 11pt; }
.toc a { color: #000; text-decoration: none; }
table { border-collapse: collapse; width: 100%; margin: 4pt 0 12pt; font-size: 9.5pt; line-height: 1.25; }
caption { caption-side: top; text-align: left; font-weight: bold; font-size: 10.5pt; margin-bottom: 4pt; line-height: 1.3; }
th, td { border: 0.5pt solid #555; padding: 3pt 4pt; vertical-align: top; text-align: left; }
th { background: #e9e9e9; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
table code { font-size: 8.5pt; }
td, th, code { overflow-wrap: anywhere; word-break: break-word; }
table.anx { table-layout: fixed; font-size: 8pt; line-height: 1.2; }
table.anx code { font-size: 7.5pt; }
table.anx tr { break-inside: auto; }
table.anx th:first-child, table.anx td:first-child { width: 5.5%; }
figure { margin: 8pt 0 12pt; break-inside: avoid; text-align: center; }
figure svg { width: 100%; height: auto; }
figcaption { font-size: 10.5pt; font-weight: bold; text-align: left; margin-bottom: 4pt; line-height: 1.3; }
pre { font-family: Consolas, "Courier New", monospace; font-size: 8.5pt; line-height: 1.25; white-space: pre-wrap;
      word-wrap: break-word; border: 0.5pt solid #777; background: #fafafa; padding: 6pt; margin: 4pt 0 10pt; }
pre.prompt { font-size: 9pt; }
.add { background: #dff3df; display: block; }
.del { background: #f8dcdc; display: block; }
.hunk { background: #e6ecf7; display: block; }
.anexo h1 { break-before: page; }
.nota { font-size: 10.5pt; }
"""


def esc(texto):
    return html.escape(texto, quote=False)


def leer(ruta):
    return Path(ruta).read_text(encoding='utf-8').replace('\r\n', '\n')


def lineas(ruta, desde, hasta):
    """Líneas `desde`..`hasta` (1-based, inclusive) de un archivo."""
    return '\n'.join(leer(ruta).split('\n')[desde - 1:hasta])


# ─── Markdown mínimo (para la respuesta íntegra del asistente) ────────────────

def _inline(texto):
    partes = re.split(r'(`[^`]*`)', texto)
    salida = []
    for parte in partes:
        if parte.startswith('`') and parte.endswith('`') and len(parte) >= 2:
            salida.append('<code>' + esc(parte[1:-1]) + '</code>')
        else:
            t = esc(parte)
            t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
            t = re.sub(r'(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])', r'<em>\1</em>', t)
            salida.append(t)
    return ''.join(salida)


def _celdas(linea):
    partes = linea.strip().strip('|')
    return [c.strip() for c in re.split(r'(?<!\\)\|', partes)]


def _anchos(cab):
    """Anchos de columna (%) según la forma de la tabla; None = repartir en partes iguales."""
    if len(cab) == 7 and 'Evidencia' in cab:
        return [5, 12, 9, 14, 20, 33, 7]
    if len(cab) == 7 and any('Problema' in c for c in cab):
        return [4, 10, 25, 15, 17, 16, 13]
    if len(cab) == 3:
        return [20, 30, 50]
    return None


def md_a_html(md):
    filas = md.split('\n')
    out, i = [], 0
    while i < len(filas):
        f = filas[i]
        if not f.strip():
            i += 1
        elif f.startswith('|') and i + 1 < len(filas) and re.match(r'^\|[\s:\-|]+\|\s*$', filas[i + 1]):
            cab = _celdas(f)
            i += 2
            cuerpo = []
            while i < len(filas) and filas[i].startswith('|'):
                cuerpo.append(_celdas(filas[i]))
                i += 1
            anchos = _anchos(cab)
            colgrupo = ('<colgroup>' + ''.join(f'<col style="width:{a}%">' for a in anchos) + '</colgroup>') if anchos else ''
            out.append('<table class="anx">' + colgrupo + '<thead><tr>' + ''.join(f'<th>{_inline(c)}</th>' for c in cab) + '</tr></thead><tbody>')
            for fila in cuerpo:
                out.append('<tr>' + ''.join(f'<td>{_inline(c)}</td>' for c in fila) + '</tr>')
            out.append('</tbody></table>')
        elif f.startswith('#'):
            nivel = len(f) - len(f.lstrip('#'))
            out.append(f'<h3>{_inline(f.lstrip("#").strip())}</h3>' if nivel > 1 else f'<h3><strong>{_inline(f.lstrip("#").strip())}</strong></h3>')
            i += 1
        elif f.strip() == '---':
            out.append('<hr>')
            i += 1
        elif f.lstrip().startswith('- '):
            out.append('<ul>')
            while i < len(filas) and filas[i].lstrip().startswith('- '):
                out.append(f'<li>{_inline(filas[i].lstrip()[2:])}</li>')
                i += 1
            out.append('</ul>')
        else:
            bloque = [f]
            i += 1
            while i < len(filas) and filas[i].strip() and not filas[i].startswith(('|', '#', '- ')):
                bloque.append(filas[i])
                i += 1
            out.append('<p>' + _inline(' '.join(bloque)) + '</p>')
    return '\n'.join(out)


def parche_a_html(texto):
    salida = []
    for linea in texto.split('\n'):
        clase = ''
        if linea.startswith('@@'):
            clase = 'hunk'
        elif linea.startswith('+') and not linea.startswith('+++'):
            clase = 'add'
        elif linea.startswith('-') and not linea.startswith('---'):
            clase = 'del'
        salida.append(f'<span class="{clase}">{esc(linea)}</span>' if clase else esc(linea))
    return '<pre>' + '\n'.join(salida).replace('</span>\n', '</span>') + '</pre>'


# ─── Figuras ──────────────────────────────────────────────────────────────────

SVG_ESTILO = """<defs>
<marker id="{p}u" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#333"/></marker>
<marker id="{p}h" viewBox="0 0 12 12" refX="11" refY="6" markerWidth="9" markerHeight="9" orient="auto"><path d="M1,1 L11,6 L1,11 z" fill="#fff" stroke="#333" stroke-width="1.2"/></marker>
</defs>
<style>
.t{{font-family:Arial,Helvetica,sans-serif;font-size:12px;fill:#111;text-anchor:middle}}
.b{{font-weight:bold}} .s{{font-size:11px}} .n{{font-size:10.5px;text-anchor:start}}
.bx{{fill:#fff;stroke:#333;stroke-width:1.2}} .c1{{fill:#e8f0fe}} .c2{{fill:#fff4d6}} .c3{{fill:#e5f4e5}} .c4{{fill:#efefef}}
.ex{{stroke-dasharray:5 3}} .ln{{stroke:#333;stroke-width:1.3;fill:none}}
</style>"""


def figura_a():
    p = 'fa'
    return f"""<svg viewBox="0 0 640 306" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Estructura por capas del módulo A">
{SVG_ESTILO.format(p=p)}
<rect class="bx c1" x="20" y="10" width="600" height="56"/>
<text class="t b" x="320" y="32">Casos de uso públicos (funciones)</text>
<text class="t s" x="320" y="53">take_for_review · archive_plan · approve_plan · regenerate_plan · expire_overdue_plans*</text>
<rect class="bx c4" x="20" y="100" width="170" height="96"/>
<text class="t b" x="105" y="122">Autorización</text><text class="t s" x="105" y="144">_ensure_access</text>
<rect class="bx c2" x="205" y="100" width="170" height="96"/>
<text class="t b" x="290" y="122">Reglas puras</text>
<text class="t s" x="290" y="142">ARCHIVABLE_STATUSES</text><text class="t s" x="290" y="158">APPROVAL_VALIDITY_DAYS</text><text class="t s" x="290" y="174">MSG_* (mensajes)</text>
<rect class="bx c3" x="390" y="100" width="230" height="96"/>
<text class="t b" x="505" y="122">Acceso a datos (funciones _…)</text>
<text class="t s" x="505" y="142">_lock_plan · _record_event</text><text class="t s" x="505" y="158">_set_routine_active</text>
<text class="t s" x="505" y="174">_supersede_current_approved</text><text class="t s" x="505" y="190">_lock_overdue_approved*</text>
<rect class="bx c4" x="20" y="232" width="600" height="44"/>
<text class="t b" x="320" y="259">Modelos de Django: Plan · PlanEvent · Routine</text>
<path class="ln" d="M105,66 V100" marker-end="url(#{p}u)"/><path class="ln" d="M290,66 V100" marker-end="url(#{p}u)"/>
<path class="ln" d="M505,66 V100" marker-end="url(#{p}u)"/>
<path class="ln" d="M105,196 V232" marker-end="url(#{p}u)"/><path class="ln" d="M505,196 V232" marker-end="url(#{p}u)"/>
<text class="t n" x="20" y="298">* añadido en la ampliación (estado «vencido»). Las flechas indican «usa».</text>
</svg>"""


def figura_b():
    p = 'fb'
    return f"""<svg viewBox="0 0 640 470" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Estructura del módulo B con el patrón State">
{SVG_ESTILO.format(p=p)}
<rect class="bx c1" x="20" y="10" width="400" height="40"/>
<text class="t b" x="220" y="35">API pública: 4 funciones (+ expire_overdue_plans*)</text>
<rect class="bx c1" x="20" y="75" width="400" height="52"/>
<text class="t b" x="220" y="97">PlanLifecycle (contexto)</text><text class="t s" x="220" y="116">delega en el estado actual · transition() · locked()</text>
<rect class="bx c3" x="450" y="75" width="170" height="52"/>
<text class="t b" x="535" y="97">PlanRepository</text><text class="t s" x="535" y="116">único acceso a datos</text>
<rect class="bx c4" x="450" y="165" width="170" height="40"/>
<text class="t b s" x="535" y="189">Plan · PlanEvent · Routine</text>
<path class="ln" d="M220,50 V75" marker-end="url(#{p}u)"/>
<path class="ln" d="M420,101 H450" marker-end="url(#{p}u)"/>
<path class="ln" d="M535,127 V165" marker-end="url(#{p}u)"/>
<rect class="bx c2" x="20" y="165" width="400" height="40"/>
<text class="t b" x="220" y="190">PlanState (base: rechaza toda operación)</text>
<path class="ln" d="M220,127 V165" marker-end="url(#{p}u)"/><text class="t n" x="226" y="150">usa el estado actual</text>
<rect class="bx c2" x="20" y="245" width="170" height="40"/><text class="t b s" x="105" y="269">_ArchivableState</text>
<rect class="bx c2" x="200" y="245" width="110" height="40"/><text class="t b s" x="255" y="269">SupersededState</text>
<rect class="bx c2" x="320" y="245" width="100" height="40"/><text class="t b s" x="370" y="269">ArchivedState</text>
<path class="ln" d="M105,245 L160,205" marker-end="url(#{p}h)"/><path class="ln" d="M255,245 L255,205" marker-end="url(#{p}h)"/>
<path class="ln" d="M370,245 L330,205" marker-end="url(#{p}h)"/>
<rect class="bx c2" x="20" y="325" width="105" height="40"/><text class="t b s" x="72" y="349">_PendingState</text>
<rect class="bx c2" x="135" y="325" width="105" height="40"/><text class="t b s" x="187" y="349">ApprovedState</text>
<rect class="bx c2 ex" x="250" y="325" width="105" height="40"/><text class="t b s" x="302" y="349">ExpiredState*</text>
<path class="ln" d="M72,325 L90,285" marker-end="url(#{p}h)"/><path class="ln" d="M187,325 L130,285" marker-end="url(#{p}h)"/>
<path class="ln" d="M302,325 L150,285" marker-end="url(#{p}h)"/>
<rect class="bx c2" x="15" y="405" width="100" height="40"/><text class="t b s" x="65" y="429">DraftAIState</text>
<rect class="bx c2" x="125" y="405" width="100" height="40"/><text class="t b s" x="175" y="429">InReviewState</text>
<path class="ln" d="M65,405 L60,365" marker-end="url(#{p}h)"/><path class="ln" d="M175,405 L90,365" marker-end="url(#{p}h)"/>
<text class="t n" x="260" y="415">Flecha con triángulo hueco = «hereda de».</text>
<text class="t n" x="260" y="432">Trazo discontinuo y * = añadido en la ampliación</text>
<text class="t n" x="260" y="449">(estado «vencido»).</text>
</svg>"""


# ─── Ensamblado ───────────────────────────────────────────────────────────────

def portada():
    return f"""<section class="portada">
<h1>{TITULO}</h1>
<p class="sub">{SUBTITULO}</p>
<p><strong>Actividad:</strong> Patrones de diseño en el código generado con IA</p>
<p><strong>Equipo:</strong> <span class="pend">[COMPLETAR: nombres de las personas integrantes]</span></p>
<p><strong>Curso y docente:</strong> <span class="pend">[COMPLETAR]</span></p>
<p><strong>Fecha:</strong> 15 de octubre de 2026</p>
<p><strong>Repositorio:</strong> https://github.com/Jdarenas22/profit-studio</p>
</section>"""


def anexos(suite):
    a = []
    a.append('<div class="anexo">')
    a.append('<h1>Anexo A. Prompt de consulta (íntegro)</h1>')
    a.append('<p class="nota">Texto exacto enviado al asistente en una conversación nueva (archivo <code>anexos/A-prompt-de-consulta.md</code>). En todos los prompts de los anexos, la ruta absoluta del equipo local se sustituyó por <code>&lt;RAIZ_DEL_PROYECTO&gt;</code> antes de publicar el informe; el resto del texto es íntegro.</p>')
    a.append(f'<pre class="prompt">{esc(leer(ANEXOS / "A-prompt-de-consulta.md"))}</pre>')
    a.append('</div><div class="anexo">')
    a.append('<h1>Anexo B. Respuesta íntegra del asistente</h1>')
    a.append('<p class="nota">Respuesta tal como la guardó el asistente (archivo <code>anexos/B-respuesta-del-asistente.md</code>), convertida de Markdown a HTML sin cambiar su contenido.</p>')
    a.append(md_a_html(leer(ANEXOS / 'B-respuesta-del-asistente.md')))
    a.append('</div><div class="anexo">')
    a.append('<h1>Anexo C. Prompts del experimento</h1>')
    a.append('<h2>C.1 Prompt A (control), íntegro</h2>')
    a.append('<p class="nota">Reconstrucción del prompt original. Archivo <code>anexos/C-prompt-control-A.md</code>.</p>')
    a.append(f'<pre class="prompt">{esc(leer(ANEXOS / "C-prompt-control-A.md"))}</pre>')
    a.append('<h2>C.2 Prompt B (reformulado): única diferencia con A</h2>')
    a.append('<p class="nota">B es igual a A salvo el nombre de la carpeta de salida (<code>exp_b</code> en lugar de <code>exp_a</code>) y este párrafo adicional en «Pautas de diseño» (verificado con <code>diff</code>). Archivo <code>anexos/D-prompt-reformulado-B.md</code>.</p>')
    b = leer(ANEXOS / 'D-prompt-reformulado-B.md').split('\n')
    parrafo = [x for x in b if x.startswith('- Aplica el patrón de diseño State')]
    a.append(f'<pre class="prompt">{esc(chr(10).join(parrafo))}</pre>')
    a.append('<h2>C.3 Prompt de ampliación (estado «vencido»), versión para A</h2>')
    a.append('<p class="nota">La versión para B es idéntica salvo la carpeta (<code>exp_b</code>). Archivos <code>anexos/E-prompt-ampliacion-A.md</code> y <code>E-prompt-ampliacion-B.md</code>.</p>')
    a.append(f'<pre class="prompt">{esc(leer(ANEXOS / "E-prompt-ampliacion-A.md"))}</pre>')
    a.append('</div><div class="anexo">')
    a.append('<h1>Anexo D. Fragmentos de código comparados</h1>')
    a.append('<p class="nota">Los archivos completos están en <code>anexos/experimento/</code>: las versiones generadas en <code>base/exp_a</code> y <code>base/exp_b</code> y las ampliadas en <code>exp_a</code> y <code>exp_b</code>.</p>')
    services = RAIZ / 'apps/plans/services.py'
    a.append('<h2>D.1 Original (V0): <code>apps/plans/services.py</code>, líneas 482 a 503</h2>')
    a.append(f'<pre>{esc(lineas(services, 482, 503))}</pre>')
    base_a = EXP / 'base/exp_a/plan_lifecycle.py'
    a.append('<h2>D.2 Control A: constantes y <code>archive_plan</code> (<code>plan_lifecycle.py</code>, líneas 43-47 y 144-161)</h2>')
    a.append(f'<pre>{esc(lineas(base_a, 43, 47))}\n…\n{esc(lineas(base_a, 144, 161))}</pre>')
    base_b = EXP / 'base/exp_b/plan_lifecycle.py'
    a.append('<h2>D.3 B con State: estados y contexto (<code>plan_lifecycle.py</code>, líneas 90-127, 186-194 y 262-274)</h2>')
    a.append(f'<pre>{esc(lineas(base_b, 90, 127))}\n…\n{esc(lineas(base_b, 186, 194))}\n…\n{esc(lineas(base_b, 262, 274))}</pre>')
    a.append('<h2>D.4 Diff de la ampliación en A (<code>plan_lifecycle.py</code>)</h2>')
    a.append(parche_a_html(leer(ANEXOS / 'F1-ampliacion-A-plan_lifecycle.patch')))
    a.append('<h2>D.5 Diff de la ampliación en B (<code>plan_lifecycle.py</code>)</h2>')
    a.append(parche_a_html(leer(ANEXOS / 'F2-ampliacion-B-plan_lifecycle.patch')))
    a.append('</div><div class="anexo">')
    a.append('<h1>Anexo E. Registro de verificaciones</h1>')
    a.append(f"""<table>
<caption>Tabla E1. Comprobaciones hechas por el equipo (las ejecutó Claude Code)</caption>
<thead><tr><th>Qué se comprobó</th><th>Cómo</th><th>Resultado</th></tr></thead>
<tbody>
<tr><td>Commits y firmas de modelo</td><td><code>git rev-list --count</code>; <code>git log</code> con búsqueda de «Co-Authored-By»</td><td>78 commits; Sonnet 5.5: 31, Sonnet 4.6: 23, Opus 5.5: 1</td></tr>
<tr><td>Tamaño del código de producción</td><td><code>wc -l</code> sin pruebas ni migraciones</td><td><code>plans</code>: 6 344 líneas; <code>health</code>: 1 901; 46 archivos <code>.py</code></td></tr>
<tr><td>Cifras de la consulta</td><td><code>grep -c</code>, <code>git log -S</code>, lectura de las funciones citadas</td><td>Ver Tabla 3 del informe</td></tr>
<tr><td>Menciones de patrones en el repositorio</td><td><code>git grep -c -iE</code> con los nombres de patrones</td><td>Sin resultados</td></tr>
<tr><td>Prompts A y B; ampliación en A y B</td><td><code>diff</code> entre archivos</td><td>Solo cambian el párrafo de State y el nombre de la carpeta</td></tr>
<tr><td>Pruebas de A y B tal como se generaron</td><td><code>PYTHONPATH=anexos/experimento manage.py test exp_a</code> (y <code>exp_b</code>)</td><td>A: 45 OK. B: 51 OK</td></tr>
<tr><td>Pruebas de A y B tras ampliar</td><td>Mismo comando</td><td>A: 67 OK (13,6 s). B: 73 OK (12,4 s)</td></tr>
<tr><td>Diff de la ampliación</td><td><code>diff -u</code> contra la copia guardada antes de ampliar</td><td>A: +34 y −5 (módulo), +210 y −3 (pruebas). B: +47 y −9 (módulo), +178 y −1 (pruebas)</td></tr>
<tr><td>Que el experimento no alterara el proyecto</td><td><code>git status</code> y suite completa <code>manage.py test</code></td><td>Solo archivos nuevos en <code>docs/actividad-patrones/</code>. {suite}</td></tr>
</tbody></table>""")
    a.append('</div>')
    return '\n'.join(a)


def con_ids_y_toc(cuerpo_y_anexos):
    items, contador = [], [0]

    def poner_id(m):
        contador[0] += 1
        ident = f's{contador[0]}'
        nivel = m.group(1)
        texto = m.group(2)
        items.append((nivel, ident, re.sub(r'<[^>]+>', '', texto)))
        return f'<h{nivel} id="{ident}">{texto}</h{nivel}>'

    cuerpo = re.sub(r'<h([12])>(.*?)</h\1>', poner_id, cuerpo_y_anexos, flags=re.S)
    toc = ['<section class="toc"><h1>Tabla de contenido</h1><ul>']
    for nivel, ident, texto in items:
        clase = 'n2' if nivel == '2' else 'n1'
        toc.append(f'<li class="{clase}"><a href="#{ident}">{html.unescape(texto)}</a></li>')
    toc.append('</ul></section>')
    return '\n'.join(toc), cuerpo


def numerar_figuras(cuerpo):
    fig1 = ('<figure><figcaption>Figura 1. Estructura del módulo A (control): capas con funciones</figcaption>'
            + figura_a() + '</figure>')
    fig2 = ('<figure><figcaption>Figura 2. Estructura del módulo B (reformulado): patrón State con contexto y repositorio</figcaption>'
            + figura_b() + '</figure>')
    return cuerpo.replace('<!--FIG1-->', fig1).replace('<!--FIG2-->', fig2)


def main():
    suite = sys.argv[1] if len(sys.argv) > 1 else ''
    cuerpo = numerar_figuras(leer(AQUI / 'cuerpo.html'))
    toc, texto = con_ids_y_toc(cuerpo + '\n' + anexos(suite))
    pagina = (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><title>{TITULO}</title>'
              f'<style>{CSS}</style></head><body>{portada()}{toc}{texto}</body></html>')
    destino = AQUI / 'informe.html'
    destino.write_text(pagina, encoding='utf-8')
    print('HTML:', destino)

    chrome = next((c for c in CHROME_RUTAS if c.exists()), None)
    if chrome is None:
        print('No se encontró Chrome ni Edge: abre informe.html en un navegador e imprímelo como PDF.')
        return
    pdf = AQUI / 'informe.pdf'
    cmd = [str(chrome), '--headless=new', '--disable-gpu', '--no-pdf-header-footer',
           f'--print-to-pdf={pdf}', destino.as_uri()]
    subprocess.run(cmd, check=False, timeout=180, capture_output=True)
    print('PDF:', pdf, '(existe)' if pdf.exists() else '(NO se generó)')


if __name__ == '__main__':
    main()
