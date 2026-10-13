"""Vistas del plan de alimentación: autorización (matriz del contrato, sección 13), la clienta solo
ve lo aprobado y lo suyo, elegir opción, "otra opción", intercambios, lista de compras, edición del
entrenador, bandeja e interruptores."""
from datetime import timedelta

from django.core import mail
from django.test import override_settings
from django.urls import reverse

from apps.health import services as health
from apps.plans import services
from apps.plans.models import Food, MealChoice, MealOption, MealOptionItem, Plan, PlanGenerationJob

from .helpers import NutritionScenarioTestCase


def has_milk(food):
    return 'milk' in food.allergen_tags or 'milk' in food.trace_tags


class TrainerNutritionAuthTests(NutritionScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.plan = self.nutrition_draft(self.client_a)
        self.option = MealOption.objects.filter(slot__nutrition_plan__plan=self.plan, slot__code='lunch').first()

    def urls(self):
        pk, opt = self.plan.pk, self.option.pk
        return [
            ('get', reverse('trainer_plan_detail', args=[pk])),
            ('get', reverse('trainer_meal_option_edit', args=[pk, opt])),
            ('post', reverse('trainer_meal_option_edit', args=[pk, opt])),
            ('post', reverse('trainer_meal_option_review', args=[pk, opt])),
            ('post', reverse('trainer_plan_approve', args=[pk])),
        ]

    def test_other_trainer_gets_404_client_403_anonymous_login(self):
        self.login(self.trainer_b)
        for method, url in self.urls():
            with self.subTest(user='otro entrenador', url=url):
                self.assertEqual(getattr(self.client, method)(url).status_code, 404)
        self.login(self.client_a)
        for method, url in self.urls():
            with self.subTest(user='clienta', url=url):
                self.assertEqual(getattr(self.client, method)(url).status_code, 403)
        self.client.logout()
        for method, url in self.urls():
            with self.subTest(user='anónimo', url=url):
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response['Location'])

    def test_an_option_of_another_plan_is_not_reachable_through_this_one(self):
        other = self.nutrition_draft(self.client_b)
        foreign = MealOption.objects.filter(slot__nutrition_plan__plan=other).first()
        self.login(self.boss)
        self.assertEqual(self.client.get(reverse('trainer_meal_option_edit', args=[self.plan.pk, foreign.pk])).status_code, 404)

    def test_assigned_trainer_and_superuser_see_the_review(self):
        for user in (self.trainer_a, self.boss):
            self.login(user)
            response = self.client.get(reverse('trainer_plan_detail', args=[self.plan.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['is_nutrition'])
            self.assertEqual(len(response.context['nutrition_review']['slots']), 3)
            self.assertContains(response, self.option.name)
            self.assertContains(response, 'Objetivos del día')
            self.assertIn('no-store', response['Cache-Control'])

    @override_settings(AI_PLANS_REQUIRE_PROFESSIONAL=True)
    def test_approve_button_follows_the_credential(self):
        Plan.objects.filter(pk=self.plan.pk).update(is_simulated=False)
        self.login(self.trainer_a)
        response = self.client.get(reverse('trainer_plan_detail', args=[self.plan.pk]))
        self.assertFalse(response.context['can_approve'])
        self.assertContains(response, 'credencial de nutrición')
        response = self.client.post(reverse('trainer_plan_approve', args=[self.plan.pk]))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Plan.objects.get(pk=self.plan.pk).status, Plan.STATUS_DRAFT_AI)
        self.login(self.boss)
        self.assertTrue(self.client.get(reverse('trainer_plan_detail', args=[self.plan.pk])).context['can_approve'])
        response = self.client.post(reverse('trainer_plan_approve', args=[self.plan.pk]), {'note_for_client': 'Hola'})
        self.assertRedirects(response, reverse('trainer_plan_detail', args=[self.plan.pk]), fetch_redirect_response=False)
        self.assertEqual(Plan.objects.get(pk=self.plan.pk).status, Plan.STATUS_APPROVED)

    @override_settings(AI_PLANS_REQUIRE_PROFESSIONAL=False)
    def test_suspended_validation_enables_approve_for_the_assigned_trainer(self):
        Plan.objects.filter(pk=self.plan.pk).update(is_simulated=False)
        self.login(self.trainer_a)
        response = self.client.get(reverse('trainer_plan_detail', args=[self.plan.pk]))
        self.assertTrue(response.context['can_approve'])
        self.assertNotContains(response, 'credencial de nutrición')

    def test_blockers_are_listed_and_disable_approval(self):
        Plan.objects.filter(pk=self.plan.pk).update(is_simulated=False)
        MealOption.objects.filter(slot__nutrition_plan__plan=self.plan, slot__code='lunch', position=3).update(is_active=False)
        self.login(self.boss)
        response = self.client.get(reverse('trainer_plan_detail', args=[self.plan.pk]))
        self.assertFalse(response.context['can_approve'])
        self.assertContains(response, 'No se puede aprobar todavía')


class TrainerOptionEditViewTests(NutritionScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.plan = self.nutrition_draft(self.client_a)
        self.option = MealOption.objects.filter(slot__nutrition_plan__plan=self.plan, slot__code='lunch').first()
        self.url = reverse('trainer_meal_option_edit', args=[self.plan.pk, self.option.pk])
        self.food = {f.name: f.pk for f in Food.objects.all()}
        self.login(self.trainer_a)

    def post(self, **overrides):
        data = {'name': 'Pollo con arroz', 'preparation': 'Cocina y sirve.',
                'food_id': [self.food['Pechuga de pollo cocida sin piel'], self.food['Arroz blanco cocido'], self.food['Tomate']],
                'grams': ['200', '200', '120']}
        data.update(overrides)
        return self.client.post(self.url, data)

    def test_form_shows_current_items_and_allowed_foods_only(self):
        self.make_profile(self.client_a, allergies=['peanut'], allergy_severity='mild')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['rows']), self.option.items.count())
        shown = {food.name for group in response.context['food_groups'] for food in group['foods']}
        self.assertIn('Arroz blanco cocido', shown)
        self.assertNotIn('Maní tostado sin sal', shown)
        self.assertContains(response, 'name="grams"')

    def test_valid_post_saves_and_redirects(self):
        response = self.post()
        self.assertRedirects(response, reverse('trainer_plan_detail', args=[self.plan.pk]), fetch_redirect_response=False)
        self.option.refresh_from_db()
        self.assertEqual((self.option.name, self.option.origin), ('Pollo con arroz', 'trainer'))
        self.assertEqual(self.option.items.count(), 3)
        self.assertEqual(Plan.objects.get(pk=self.plan.pk).status, Plan.STATUS_IN_REVIEW)

    def test_invalid_posts_return_400_and_keep_the_option(self):
        before = list(self.option.items.values_list('food_id', 'grams'))
        response = self.post(grams=['200', 'mucho', '120'])
        self.assertEqual(response.status_code, 400)
        self.assertIn('items', response.context['errors'])
        response = self.post(food_id=[999999, self.food['Tomate'], self.food['Lechuga']], grams=['100', '100', '100'])
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.context['error_list'])
        response = self.post(preparation='Toma un suplemento')
        self.assertEqual(response.status_code, 400)
        response = self.post(name='x' * 101)
        self.assertEqual(response.status_code, 400)
        self.assertIn('name', response.context['errors'])
        self.assertEqual(list(self.option.items.values_list('food_id', 'grams')), before)

    def test_out_of_band_energy_saves_with_a_warning(self):
        self.post(food_id=[self.food['Lechuga'], self.food['Tomate']], grams=['100', '100'])
        self.assertContains(self.client.get(reverse('trainer_plan_detail', args=[self.plan.pk])), 'Energía fuera de ±5 %')

    def test_blocked_when_the_client_is_not_evaluable(self):
        self.make_profile(self.client_a, pregnancy_status='pregnant')
        response = self.client.get(self.url, follow=True)
        self.assertRedirects(response, reverse('trainer_plan_detail', args=[self.plan.pk]))
        self.assertContains(response, 'No se puede editar ahora')


class MemberNutritionViewsTests(NutritionScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.plan = self.approved_nutrition(self.client_a)
        self.slot = self.plan.nutrition.slots.get(code='lunch')
        self.option = self.slot.options.get(position=1)
        self.today = health.dates.today()
        self.login(self.client_a)

    def test_page_shows_three_options_per_meal_and_the_disclaimer(self):
        response = self.client.get(reverse('member_nutrition_plan'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual([len(entry['options']) for entry in response.context['slots']], [3, 3, 3])
        self.assertContains(response, 'No reemplaza la valoración')
        self.assertContains(response, self.option.name)
        self.assertContains(response, 'Buen provecho')                       # nota del entrenador
        self.assertContains(response, 'RETHUS')                              # credencial de quien aprobó
        self.assertEqual(len(response.context['date_pills']), 7)
        self.assertIsNone(response.context['day_totals'])
        self.assertEqual(response.context['targets']['kcal'], self.plan.nutrition.kcal_target)

    def test_nothing_before_approval_and_nothing_from_other_clients(self):
        draft = self.nutrition_draft(self.client_b)
        self.login(self.client_b)
        response = self.client.get(reverse('member_nutrition_plan'), follow=True)
        self.assertRedirects(response, reverse('member_plans'))
        self.assertNotContains(response, MealOption.objects.filter(slot__nutrition_plan__plan=draft).first().name)
        self.assertTrue(response.context['pending_nutrition'])
        self.assertIsNone(response.context['nutrition_plan'])
        for name in ('member_exchanges', 'member_shopping_list'):
            self.assertRedirects(self.client.get(reverse(name)), reverse('member_plans'), fetch_redirect_response=False)

    def test_role_and_login_rules(self):
        urls = [reverse('member_nutrition_plan'), reverse('member_exchanges'), reverse('member_shopping_list')]
        self.login(self.trainer_a)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse('login'), response['Location'])

    def test_choose_an_option_and_see_the_day_total(self):
        response = self.client.post(reverse('member_meal_choice'), {
            'slot': self.slot.pk, 'option': self.option.pk, 'date': self.today.isoformat()})
        self.assertRedirects(response, reverse('member_nutrition_plan') + f'?date={self.today.isoformat()}#slot-{self.slot.pk}',
                             fetch_redirect_response=False)
        self.assertEqual(MealChoice.objects.get().option, self.option)
        page = self.client.get(reverse('member_nutrition_plan'))
        self.assertEqual(page.context['chosen_count'], 1)
        self.assertTrue(page.context['day_totals']['kcal'])
        self.assertContains(page, 'Elegida para este día')

    def test_bad_input_is_refused_without_creating_anything(self):
        for data in ({'slot': 'x', 'option': self.option.pk, 'date': self.today.isoformat()},
                     {'slot': self.slot.pk, 'option': self.option.pk, 'date': 'ayer'},
                     {'slot': self.slot.pk, 'option': self.option.pk,
                      'date': (self.today + timedelta(days=9)).isoformat()}):
            response = self.client.post(reverse('member_meal_choice'), data, follow=True)
            self.assertEqual(response.status_code, 200)
        self.assertFalse(MealChoice.objects.exists())

    def test_other_clients_slot_or_option_is_a_404(self):
        other = self.approved_nutrition(self.client_b)
        foreign_slot = other.nutrition.slots.get(code='lunch')
        foreign_option = foreign_slot.options.first()
        for slot, option in ((foreign_slot, foreign_option), (self.slot, foreign_option)):
            response = self.client.post(reverse('member_meal_choice'), {
                'slot': slot.pk, 'option': option.pk, 'date': self.today.isoformat()})
            self.assertEqual(response.status_code, 404)
        response = self.client.post(reverse('member_meal_alternative', args=[foreign_slot.pk]), {
            'option': foreign_option.pk, 'request_token': services.new_request_token()})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(MealChoice.objects.exists())
        self.assertFalse(PlanGenerationJob.objects.filter(kind='meal_alternative').exists())

    def test_an_option_that_no_longer_fits_the_client_is_flagged_and_cannot_be_chosen(self):
        milk = Food.objects.get(name='Leche entera')
        MealOptionItem.objects.filter(option=self.option).update(food=milk)
        self.make_profile(self.client_a, allergies=['milk'], allergy_severity='mild')
        page = self.client.get(reverse('member_nutrition_plan'))
        flagged = [opt for entry in page.context['slots'] for opt in entry['options'] if opt['conflict']]
        self.assertIn(self.option, [opt['option'] for opt in flagged])
        for opt in flagged:                      # (el generador de ejemplo puede haber usado queso en otra opción)
            self.assertTrue(any(has_milk(item['food']) for item in opt['items']), opt['option'])
        unflagged = [opt for entry in page.context['slots'] for opt in entry['options'] if not opt['conflict']]
        self.assertFalse(any(has_milk(item['food']) for opt in unflagged for item in opt['items']))
        self.assertContains(page, 'ya no es compatible con tu ficha')
        response = self.client.post(reverse('member_meal_choice'), {
            'slot': self.slot.pk, 'option': self.option.pk, 'date': self.today.isoformat()}, follow=True)
        self.assertContains(response, 'ya no es compatible')
        self.assertFalse(MealChoice.objects.exists())

    def test_exchanges_and_shopping_list(self):
        response = self.client.get(reverse('member_exchanges'))
        self.assertContains(response, 'Cereales y derivados')
        self.assertContains(response, 'Arroz blanco cocido')
        self.assertContains(response, 'No reemplaza la valoración')
        response = self.client.get(reverse('member_shopping_list'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['rows'])
        self.assertEqual(response.context['assumed'], 21)
        self.assertContains(response, 'rotas entre A, B y C')

    def test_other_option_flow(self):
        with self.fake_ai(), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('member_meal_alternative', args=[self.slot.pk]), {
                'option': self.option.pk, 'request_token': services.new_request_token()})
        job = PlanGenerationJob.objects.get(kind='meal_alternative')
        self.assertRedirects(response, reverse('member_plan_request_status', args=[job.pk]), fetch_redirect_response=False)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        status = self.client.get(reverse('member_plan_request_status', args=[job.pk]))
        self.assertContains(status, 'Opción lista')
        self.assertContains(status, reverse('member_nutrition_plan'))
        poll = self.client.get(reverse('member_plan_request_poll', args=[job.pk]))
        self.assertEqual(poll.status_code, 286)
        page = self.client.get(reverse('member_nutrition_plan'))
        self.assertContains(page, 'pendiente de revisión del entrenador')
        self.assertNotContains(page, self.option.name)
        self.login(self.client_b)
        self.assertEqual(self.client.get(reverse('member_plan_request_status', args=[job.pk])).status_code, 404)

    @override_settings(AI_PLANS_ALT_SLOT_WEEKLY_LIMIT=0)
    def test_other_option_quota_shows_a_message(self):
        response = self.client.post(reverse('member_meal_alternative', args=[self.slot.pk]), {
            'option': self.option.pk, 'request_token': services.new_request_token()}, follow=True)
        self.assertContains(response, 'esta semana')
        self.assertFalse(PlanGenerationJob.objects.filter(kind='meal_alternative').exists())
        self.assertNotContains(response, f'action="{reverse("member_meal_alternative", args=[self.slot.pk])}"')

    def test_other_option_needs_a_token_and_the_member_role(self):
        response = self.client.post(reverse('member_meal_alternative', args=[self.slot.pk]), {'option': self.option.pk},
                                    follow=True)
        self.assertContains(response, 'expiró')
        self.login(self.trainer_a)
        self.assertEqual(self.client.post(reverse('member_meal_alternative', args=[self.slot.pk]), {
            'option': self.option.pk, 'request_token': services.new_request_token()}).status_code, 403)

    def test_member_plans_portal_links_to_the_nutrition_page(self):
        response = self.client.get(reverse('member_plans'))
        self.assertContains(response, reverse('member_nutrition_plan'))
        self.assertEqual(response.context['nutrition_plan'], self.plan)
        self.assertContains(response, 'RETHUS')


class RequestAndTrainerFlowTests(NutritionScenarioTestCase):
    def test_request_page_has_both_kinds_and_posts_nutrition(self):
        self.ready(self.client_a)
        self.login(self.client_a)
        response = self.client.get(reverse('member_plan_request'), {'kind': 'nutrition'})
        self.assertEqual(response.context['kind'], 'nutrition')
        self.assertEqual([k['code'] for k in response.context['kinds']], ['exercise', 'nutrition'])
        self.assertTrue(response.context['eligibility']['can_request'])
        self.assertContains(response, 'name="kind" value="nutrition"')
        with self.fake_ai(), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('member_plan_request'), {
                'kind': 'nutrition', 'request_token': services.new_request_token()})
        job = PlanGenerationJob.objects.get(client=self.client_a)
        self.assertEqual((job.kind, job.status), ('nutrition', PlanGenerationJob.STATUS_SUCCEEDED))
        self.assertRedirects(response, reverse('member_plan_request_status', args=[job.pk]), fetch_redirect_response=False)
        status = self.client.get(reverse('member_plan_request_status', args=[job.pk]))
        self.assertContains(status, 'Borrador listo')

    def test_missing_requirements_link_back_to_the_nutrition_request(self):
        self.login(self.client_b)
        response = self.client.get(reverse('member_plan_request'), {'kind': 'nutrition'})
        urls = [item['url'] for item in response.context['eligibility']['missing'] if item['url']]
        self.assertTrue(urls)
        self.assertTrue(any('next=/plans/request/%3Fkind%3Dnutrition' in url for url in urls), urls)

    def test_blocked_nutrition_request_notifies_without_content(self):
        self.ready(self.client_a, eating_disorder_history='yes')
        self.login(self.client_a)
        response = self.client.get(reverse('member_plan_request'), {'kind': 'nutrition'})
        self.assertTrue(response.context['eligibility']['blocked'])
        self.assertNotContains(response, 'trastorno')
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('member_plan_request'), {'kind': 'nutrition', 'request_token': services.new_request_token()})
        job = PlanGenerationJob.objects.get(client=self.client_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_BLOCKED)
        self.assertEqual(len(mail.outbox), 1)

    def test_trainer_generates_nutrition_from_the_client_page(self):
        self.ready(self.client_a)
        self.login(self.trainer_a)
        detail = self.client.get(reverse('trainer_client_detail', args=[self.client_a.pk]))
        self.assertContains(detail, 'Generar borrador de alimentación con IA')
        self.assertNotEqual(detail.context['plan_request_token'], detail.context['plan_request_token_nutrition'])
        with self.fake_ai(), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('trainer_plan_generate', args=[self.client_a.pk]), {
                'kind': 'nutrition', 'request_token': services.new_request_token()})
        job = PlanGenerationJob.objects.get()
        self.assertEqual((job.kind, job.requested_by), ('nutrition', self.trainer_a))
        self.assertRedirects(response, reverse('trainer_plan_job', args=[job.pk]), fetch_redirect_response=False)
        page = self.client.get(reverse('trainer_plan_job', args=[job.pk]))
        self.assertContains(page, 'Revisar el borrador')
        # El tipo por omisión sigue siendo ejercicio
        with self.fake_ai(), self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('trainer_plan_generate', args=[self.client_a.pk]), {
                'request_token': services.new_request_token()})
        self.assertEqual(PlanGenerationJob.objects.filter(kind='exercise').count(), 1)

    def test_blocked_nutrition_job_page_for_the_trainer(self):
        self.ready(self.client_a)
        Food.objects.update(is_active=False)
        job, _ = self.request_nutrition(self.client_a, actor=self.trainer_a)
        self.login(self.trainer_a)
        page = self.client.get(reverse('trainer_plan_job', args=[job.pk]))
        self.assertContains(page, 'D06_FEW_FOODS')
        self.assertContains(page, 'guía de alimentación aparte')

    def test_inbox_marks_options_requested_by_the_client(self):
        plan = self.approved_nutrition(self.client_a)
        slot = plan.nutrition.slots.get(code='lunch')
        self.login(self.trainer_a)
        self.assertEqual(self.client.get(reverse('trainer_plan_inbox')).context['rows'], [])
        with self.fake_ai(), self.captureOnCommitCallbacks(execute=True):
            from apps.plans import nutrition
            nutrition.request_alternative(self.client_a, slot.pk, slot.options.get(position=1).pk,
                                          services.new_request_token())
        response = self.client.get(reverse('trainer_plan_inbox'))
        rows = response.context['rows']
        self.assertEqual([(row['plan'], row['client_regenerated']) for row in rows], [(plan, True)])
        self.assertContains(response, 'Opción regenerada por la clienta')
        self.assertEqual(response.context['pending_count'], 1)
        # El detalle del plan aprobado muestra la opción pendiente y deja revisarla
        detail = self.client.get(reverse('trainer_plan_detail', args=[plan.pk]))
        self.assertEqual(detail.context['nutrition_review']['needs_review_count'], 1)
        new = slot.options.get(position=1, is_active=True)
        self.assertContains(detail, reverse('trainer_meal_option_review', args=[plan.pk, new.pk]))
        response = self.client.post(reverse('trainer_meal_option_review', args=[plan.pk, new.pk]), {'action': 'keep'})
        self.assertRedirects(response, reverse('trainer_plan_detail', args=[plan.pk]), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('trainer_plan_inbox')).context['rows'], [])
        response = self.client.post(reverse('trainer_meal_option_review', args=[plan.pk, new.pk]), {'action': 'borrar'}, follow=True)
        self.assertContains(response, 'Elige qué hacer')

    def test_revert_through_the_view(self):
        plan = self.approved_nutrition(self.client_a)
        slot = plan.nutrition.slots.get(code='dinner')
        old = slot.options.get(position=3)
        with self.fake_ai(), self.captureOnCommitCallbacks(execute=True):
            from apps.plans import nutrition
            nutrition.request_alternative(self.client_a, slot.pk, old.pk, services.new_request_token())
        new = slot.options.get(position=3, is_active=True)
        self.login(self.trainer_a)
        self.client.post(reverse('trainer_meal_option_review', args=[plan.pk, new.pk]), {'action': 'revert'})
        old.refresh_from_db()
        self.assertTrue(old.is_active)


class NutritionSwitchTests(NutritionScenarioTestCase):
    def test_everything_is_404_when_off(self):
        plan = self.approved_nutrition(self.client_a)
        slot = plan.nutrition.slots.first()
        option = slot.options.first()
        member = [('get', reverse('member_nutrition_plan')), ('get', reverse('member_exchanges')),
                  ('get', reverse('member_shopping_list')), ('post', reverse('member_meal_choice')),
                  ('post', reverse('member_meal_alternative', args=[slot.pk]))]
        trainer = [('get', reverse('trainer_meal_option_edit', args=[plan.pk, option.pk])),
                   ('post', reverse('trainer_meal_option_review', args=[plan.pk, option.pk]))]
        for settings in ({'AI_PLANS_MODE': 'off'}, {'HEALTH_FEATURES_ENABLED': False}):
            with self.settings(**settings):
                for user, routes in ((self.client_a, member), (self.trainer_a, trainer)):
                    self.login(user)
                    for method, url in routes:
                        with self.subTest(settings=settings, url=url):
                            self.assertEqual(getattr(self.client, method)(url).status_code, 404)
