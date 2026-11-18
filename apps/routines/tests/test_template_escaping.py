"""Textos de usuarios dentro de expresiones JS (Alpine e inline): XSS almacenado.

Django escapa ' como &#x27; en HTML, pero el navegador decodifica la entidad del atributo
antes de que Alpine (o un onsubmit) evalúe la expresión: el autoescape no basta ahí.
"""
import re

from django.urls import reverse

from apps.accounts.tests.helpers import ScenarioTestCase
from apps.exercises.models import Exercise, ExerciseCategory
from apps.routines.models import Routine

PAYLOAD = r"""x'||alert(1)||' " \ </script><img src=x onerror=alert(2)> ${alert(3)}"""


class RoutineBuilderExerciseFilterTests(ScenarioTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        category = ExerciseCategory.objects.create(name='Cat ' + PAYLOAD)
        cls.evil = Exercise.objects.create(
            name='Ejer ' + PAYLOAD, category=category, level=Exercise.LEVEL_BEGINNER,
            description='x', muscles='y',
        )

    def option_tag(self, html):
        match = re.search(r'<option value="%d"[^>]*>' % self.evil.pk, html)
        self.assertIsNotNone(match, 'el ejercicio debe aparecer en el selector')
        return match.group(0)

    def test_exercise_text_never_enters_the_alpine_expression(self):
        self.login(self.trainer_a)
        html = self.client.get(reverse('trainer_routine_builder', args=[self.routine_a.pk])).content.decode()
        tag = self.option_tag(html)
        x_show = re.search(r'x-show="([^"]*)"', tag).group(1)
        self.assertNotIn('alert', x_show)
        self.assertNotIn('&#x27;', x_show)
        self.assertIn('$el.dataset.name', x_show)
        self.assertIn('$el.dataset.category', x_show)
        # el texto va en data-*, en minúsculas y escapado como HTML
        self.assertIn(r'data-name="ejer x&#x27;||alert(1)||&#x27; &quot; \ &lt;/script&gt;', tag)
        self.assertIn('data-category="cat x&#x27;||alert(1)||&#x27;', tag)
        self.assertNotIn("x'||alert", html)
        self.assertNotIn('<img src=x', html)

    def test_htmx_fragment_uses_the_same_safe_markup(self):
        """El fragmento del día (respuesta HTMX al agregar un día) también lista los ejercicios."""
        self.login(self.trainer_a)
        response = self.client.post(reverse('trainer_add_day', args=[self.routine_a.pk]), {'day_name': 'Dia 2'})
        tag = self.option_tag(response.content.decode())
        self.assertNotIn('alert', re.search(r'x-show="([^"]*)"', tag).group(1))


class RoutineListDeleteConfirmTests(ScenarioTestCase):
    def test_routine_name_is_js_escaped_in_onsubmit(self):
        Routine.objects.create(name='R ' + PAYLOAD, user=self.client_a, trainer=self.trainer_a)
        self.login(self.trainer_a)
        html = self.client.get(reverse('trainer_routine_list')).content.decode()
        handler = re.search(r'onsubmit="(return confirm\([^"]*R x[^"]*)"', html).group(1)
        self.assertIn(r'\u0027||alert(1)||\u0027', handler)
        self.assertNotIn('&#x27;', handler)
        self.assertNotIn('</script>', html.split('R x', 1)[1].split('</form>', 1)[0])
