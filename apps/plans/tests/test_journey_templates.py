"""Plantillas del camino guiado (templates/plans/partials/journey_card.html).

Se renderiza el parcial con contextos falsos que siguen el contrato de `journey`
(docs/contracts/camino-guiado.md), sin depender de la lógica del backend.
"""
import copy
import re
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase

PARTIAL = 'plans/partials/journey_card.html'
TEMPLATES = Path(settings.BASE_DIR) / 'templates'

STEP_DEFS = [
    ('consent_health', 'Autoriza el uso de tus datos de salud', '/health/consent/health_data/', 'Dar mi autorización', 'all'),
    ('profile', 'Completa tu ficha', '/health/profile/', 'Llenar mi ficha', 'all'),
    ('measure', 'Registra tus medidas', '/assessments/me/measurements/add/', 'Registrar medidas', 'all'),
    ('consent_ai', 'Autoriza el apoyo de la inteligencia artificial', '/health/consent/ai_processing/', 'Autorizar', 'all'),
    ('activity', 'Cuéntanos tu nivel de actividad', '/health/profile/?edit=1', 'Completar más detalles', 'nutrition'),
]

# Códigos internos que nunca deben verse en el HTML (pasos, elegibilidad, banderas, estados).
TECH_CODES = re.compile(
    r'consent_health|consent_ai|missing_steps|in_review|\bD0\d|\bR0\d|\bY0\d|\bM0\d|\bV0\d|_FEW_|'
    r'\bdone\b|\btodo\b|\blocked\b|\bquota\b|\bgenerating\b'
)


def make_journey(statuses, plans=None, all_ready=False, all_done=None):
    steps = []
    for (code, title, url, cta, scope), status in zip(STEP_DEFS, statuses):
        steps.append({
            'code': code, 'title': title, 'description': f'Descripción de «{title}».',
            'status': status, 'url': None if status == 'locked' else url,
            'cta': None if status == 'locked' else cta, 'scope': scope,
        })
    next_step = next((s for s in steps if s['status'] == 'todo'), None)
    missing = {'state': 'missing_steps', 'url': None, 'cta': None,
               'message': 'Completa los pasos para pedir este plan.'}
    return {
        'steps': steps,
        'done_count': sum(1 for s in steps if s['status'] == 'done'),
        'total': len(steps),
        'next_step': next_step,
        'plans': plans or {'exercise': dict(missing), 'nutrition': dict(missing)},
        'all_ready': all_ready,
        'all_done': all(s['status'] == 'done' for s in steps) if all_done is None else all_done,
    }


def plan(state, url='/plans/x/', cta='Ir', message='Mensaje para la clienta.'):
    return {'state': state, 'url': url, 'cta': cta, 'message': message}


def visible_html(html):
    """El HTML sin atributos class (sus nombres no los ve la clienta)."""
    return re.sub(r'class="[^"]*"', '', html)


class JourneyCardTests(SimpleTestCase):
    def render(self, journey, **extra):
        return render_to_string(PARTIAL, {'journey': journey, **extra})

    def assertNoTechCodes(self, html):
        self.assertIsNone(TECH_CODES.search(visible_html(html)), TECH_CODES.search(visible_html(html)))

    def test_start(self):
        journey = make_journey(['todo', 'locked', 'locked', 'locked', 'locked'])
        html = self.render(journey)
        self.assertIn('Tu camino a tu plan', html)
        self.assertIn('Paso 1 de 5', html)
        self.assertIn('0 de 5 hechos', html)
        self.assertIn('<progress', html)
        self.assertIn('max="5" value="0"', html)
        self.assertIn('Siguiente', html)
        self.assertIn('Después', html)
        self.assertIn('href="/health/consent/health_data/"', html)
        self.assertIn('Dar mi autorización', html)
        self.assertEqual(html.count('aria-current="step"'), 1)
        self.assertNoTechCodes(html)

    def test_halfway(self):
        journey = make_journey(['done', 'done', 'todo', 'locked', 'locked'])
        html = self.render(journey)
        self.assertIn('Paso 3 de 5', html)
        self.assertIn('2 de 5 hechos', html)
        self.assertIn('max="5" value="2"', html)
        self.assertIn('Hecho', html)
        self.assertIn('Tu siguiente paso: Registra tus medidas.', html)
        self.assertIn('Solo para alimentación', html)
        # El paso siguiente se destaca con su botón
        self.assertRegex(html, r'aria-current="step"[\s\S]*Registrar medidas')
        # Los pasos hechos se pueden revisar
        self.assertIn('Revisar', html)
        # Sin plan disponible todavía no hay tarjetas de plan
        self.assertNotIn('Plan de ejercicio', html)
        self.assertNoTechCodes(html)

    def test_locked_steps_are_not_links(self):
        journey = make_journey(['done', 'todo', 'locked', 'locked', 'locked'])
        html = self.render(journey)
        items = re.findall(r'<li class="pfs-step[^"]*"[\s\S]*?</li>', html)
        self.assertEqual(len(items), 5)
        for item, step in zip(items, journey['steps']):
            if step['status'] == 'locked':
                self.assertIn('pfs-step-locked', item)
                self.assertNotIn('<a ', item)
                self.assertIn('Después', item)

    def test_exercise_available_before_nutrition_step(self):
        journey = make_journey(['done', 'done', 'done', 'done', 'todo'], plans={
            'exercise': plan('ready', url='/plans/request/?kind=exercise'),
            'nutrition': plan('missing_steps', url='/health/profile/?edit=1', cta='Completar más detalles'),
        })
        html = self.render(journey)
        self.assertIn('Paso 5 de 5', html)
        self.assertIn('Pedir mi plan de ejercicio', html)
        self.assertIn('href="/plans/request/?kind=exercise"', html)
        self.assertIn('Plan de alimentación', html)
        # El paso pendiente ya trae su botón; la tarjeta de alimentación no lo repite
        self.assertEqual(html.count('href="/health/profile/?edit=1"'), 1)
        self.assertNoTechCodes(html)

    def test_all_ready_ready_state(self):
        journey = make_journey(['done'] * 5, all_ready=True, plans={
            'exercise': plan('ready', url='/plans/request/?kind=exercise'),
            'nutrition': plan('ready', url='/plans/request/?kind=nutrition'),
        })
        html = self.render(journey)
        self.assertIn('5 de 5 pasos hechos', html)
        self.assertIn('<details', html)
        self.assertIn('Ver los pasos que completaste', html)
        self.assertIn('Pedir mi plan de ejercicio', html)
        self.assertIn('Pedir mi plan de alimentación', html)
        # Las tarjetas van antes de la lista plegada
        self.assertLess(html.index('Pedir mi plan de ejercicio'), html.index('<details'))
        self.assertNotIn('aria-current="step"', html)
        self.assertNoTechCodes(html)

    def test_all_ready_each_state(self):
        expectations = {
            'generating': ('En preparación', 'Ver cómo va', True),
            'in_review': ('En revisión', None, False),
            'approved': ('Aprobado', 'Ver mi plan', True),
            'blocked': ('En pausa', None, False),
            'quota': ('Vuelve más adelante', None, False),
            'missing_steps': ('Faltan pasos', 'Completar más detalles', True),
        }
        for state, (text, button, has_link) in expectations.items():
            with self.subTest(state=state):
                journey = make_journey(['done'] * 5, all_ready=True, plans={
                    'exercise': plan(state, url='/plans/estado/', cta='Completar más detalles',
                                     message='Mensaje para la clienta.'),
                    'nutrition': plan('ready', url='/plans/request/?kind=nutrition'),
                })
                html = self.render(journey)
                card = re.search(r'<article[\s\S]*?</article>', html).group(0)
                self.assertIn('Plan de ejercicio', card)
                self.assertIn(text, card)
                self.assertIn('Mensaje', card)
                if button:
                    self.assertIn(button, card)
                self.assertEqual('href="/plans/estado/"' in card, has_link)
                self.assertNoTechCodes(html)

    def test_generic_text_when_message_is_empty(self):
        journey = make_journey(['done'] * 5, all_ready=True, plans={
            'exercise': plan('generating', message=''),
            'nutrition': plan('in_review', url=None, cta=None, message=''),
        })
        html = self.render(journey)
        self.assertIn('Estamos armando tu plan.', html)
        self.assertIn('Tu entrenador lo está revisando.', html)

    def test_all_ready_but_activity_pending(self):
        # all_ready con 4 de 5: falta solo el paso de alimentación (contrato, all_done)
        journey = make_journey(['done', 'done', 'done', 'done', 'todo'], all_ready=True, plans={
            'exercise': plan('ready', url='/plans/request/?kind=exercise'),
            'nutrition': plan('missing_steps', url='/health/profile/?edit=1#mas-detalles',
                              cta='Completar más detalles'),
        })
        html = self.render(journey)
        self.assertNotIn('5 de 5 pasos hechos', html)
        self.assertIn('Paso 5 de 5', html)
        self.assertIn('Ver tus pasos (4 de 5 hechos)', html)
        self.assertIn('Para el plan de alimentación te falta', html)
        # Con la lista plegada, la tarjeta de alimentación lleva al paso pendiente
        card = re.findall(r'<article[\s\S]*?</article>', html)[1]
        self.assertIn('href="/health/profile/?edit=1#mas-detalles"', card)
        self.assertNoTechCodes(html)

    def test_message_is_escaped(self):
        journey = make_journey(['done'] * 5, all_ready=True, plans={
            'exercise': plan('blocked', message='<script>alert(1)</script>'),
            'nutrition': plan('quota'),
        })
        html = self.render(journey)
        self.assertNotIn('<script>alert', html)
        self.assertIn('&lt;script&gt;', html)

    def test_compact_has_progress_but_no_buttons_or_cards(self):
        journey = make_journey(['done', 'done', 'todo', 'locked', 'locked'], plans={
            'exercise': plan('ready'), 'nutrition': plan('ready'),
        })
        html = self.render(journey, compact=True)
        self.assertIn('Tu camino a tu plan', html)
        self.assertIn('Paso 3 de 5', html)
        self.assertIn('Hecho: </span>Autoriza el uso de tus datos de salud', html)
        self.assertIn('Hecho: </span>Completa tu ficha', html)
        # Lo pendiente lo lista «Pedir mi plan» debajo: aquí solo lo hecho
        self.assertNotIn('Registra tus medidas', html)
        self.assertNotIn('<a ', html)
        self.assertNotIn('<article', html)
        self.assertNotIn('journey-title"', html)
        self.assertNoTechCodes(html)

    def test_contract_copy_is_not_mutated(self):
        journey = make_journey(['done', 'todo', 'locked', 'locked', 'locked'])
        before = copy.deepcopy(journey)
        self.render(journey)
        self.assertEqual(journey, before)


class JourneyIncludeTests(SimpleTestCase):
    """Con journey=None las páginas no dibujan nada nuevo."""

    def test_dashboard_guards_include(self):
        source = (TEMPLATES / 'accounts' / 'member_dashboard.html').read_text(encoding='utf-8')
        self.assertIn("{% elif journey %}{% include 'plans/partials/journey_card.html' %}{% endif %}", source)
        # El bloque «Mi perfil para los planes» solo sale cuando no se ve el camino guiado
        self.assertRegex(source, r'{% if not journey or [^%]*%}\s*<section aria-labelledby="dash-health-title"')

    def test_forms_keep_next(self):
        hidden = '{% if next %}<input type="hidden" name="next" value="{{ next }}">{% endif %}'
        for name in ('health/partials/profile_wizard.html', 'health/profile_detail.html',
                     'assessments/member_measurement_add.html', 'health/consent.html'):
            with self.subTest(template=name):
                self.assertIn(hidden, (TEMPLATES / name).read_text(encoding='utf-8'))

    def test_request_guards_compact_include(self):
        source = (TEMPLATES / 'plans' / 'request.html').read_text(encoding='utf-8')
        self.assertIn(
            "{% if journey %}{% include 'plans/partials/journey_card.html' with compact=True %}{% endif %}",
            source,
        )
        # La lista de lo que falta y el formulario siguen ahí
        self.assertIn('eligibility.missing', source)
        self.assertIn('name="request_token"', source)

    def test_single_line_comments_only(self):
        # {# ... #} de varias líneas no es un comentario en Django: se vería en la página
        for name in ('accounts/member_dashboard.html', 'plans/request.html',
                     'plans/partials/journey_card.html', 'plans/partials/journey_plan.html'):
            for number, line in enumerate((TEMPLATES / name).read_text(encoding='utf-8').splitlines(), 1):
                if '{#' in line:
                    self.assertIn('#}', line, f'{name}:{number}')
