from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone
from learning.models import Practice, Task, TaskBlock, Topic
from learning.security import form_token


class CatalogAnswerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('inline-student')
        cls.other = get_user_model().objects.create_user('other-inline-student')
        cls.topic = Topic.objects.create(number=1, title='Графы')
        cls.task = Task.objects.create(topic=cls.topic, title='Условие', answer='42', solution='SECRET_SOLUTION')
        TaskBlock.objects.create(task=cls.task, kind='text', purpose='solution', text='SECRET_BLOCK')

    def setUp(self):
        self.client.force_login(self.user)

    def submit(self, task=None, answer='42', token=None, ajax=True, **fields):
        task = task or self.task
        data = {'task_id': str(task.pk), f'task-{task.pk}-answer': answer,
                'submit_token': token or form_token(self.user, f'task:{task.pk}')}
        data.update(fields)
        return self.client.post('/catalog/?number=1&page=1&q=', data,
            **({'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'} if ajax else {}))

    def test_cards_have_unique_inline_forms_and_no_solve_navigation(self):
        second = Task.objects.create(topic=self.topic, title='Второе', answer='9')
        response = self.client.get('/catalog/?number=1')
        self.assertContains(response, 'data-catalog-answer', count=2)
        for task in (self.task, second):
            self.assertContains(response, f'id="id_task-{task.pk}-answer"')
            self.assertNotContains(response, f'href="/tasks/{task.pk}/"')
        self.assertNotContains(response, 'Решать ↗')

    def test_ajax_checks_saves_and_reveals_only_current_task(self):
        response = self.submit()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['errors'], {})
        self.assertTrue(response.json()['solved'])
        self.assertIn('✓ Верно', response.json()['feedback'])
        self.assertIn('SECRET_BLOCK', response.json()['feedback'])
        self.assertTrue(Practice.objects.get().correct)

    def test_wrong_answer_and_private_history_do_not_leak_to_another_user(self):
        self.assertFalse(self.submit(answer='17').json()['solved'])
        self.client.force_login(self.other)
        response = self.client.get('/catalog/?number=1')
        self.assertNotContains(response, 'SECRET_SOLUTION')
        self.assertNotContains(response, 'SECRET_BLOCK')
        self.assertNotContains(response, '<code>17</code>')

    def test_retry_token_is_idempotent_and_scoped(self):
        token = form_token(self.user, f'task:{self.task.pk}')
        self.submit(token=token)
        self.submit(token=token)
        self.assertEqual(Practice.objects.count(), 1)
        wrong = form_token(self.other, f'task:{self.task.pk}')
        self.assertEqual(self.submit(token=wrong).status_code, 403)
        self.assertEqual(self.submit(token=form_token(self.user, 'task:999999')).status_code, 403)

    def test_blank_invalid_or_missing_tokens_do_not_create_attempts(self):
        response = self.submit(answer='')
        self.assertIn('answer', response.json()['errors'])
        response = self.submit(answer='x' * 251)
        self.assertIn('answer', response.json()['errors'])
        self.assertEqual(self.client.post('/catalog/', {'task_id': self.task.pk}).status_code, 403)
        self.assertFalse(Practice.objects.exists())

    def test_draft_archived_and_missing_tasks_are_inaccessible(self):
        self.task.published = False
        self.task.save()
        self.assertEqual(self.submit().status_code, 404)
        self.task.published = True
        self.task.archived_at = timezone.now()
        self.task.save()
        self.assertEqual(self.submit().status_code, 404)
        self.assertFalse(Practice.objects.exists())

    def test_csrf_is_required_for_ajax_and_login_is_required(self):
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(csrf.post('/catalog/', {'task_id': self.task.pk}, HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code, 403)
        self.client.logout()
        self.assertEqual(self.submit().status_code, 302)
        self.assertFalse(Practice.objects.exists())

    def test_code_policy_and_escaping_apply_inline(self):
        self.task.code_mode = 'required'
        self.task.save()
        self.assertIn('python_code', self.submit().json()['errors'])
        code = '<script>test()</script>\nprint(42)'
        result = self.submit(**{f'task-{self.task.pk}-python_code': code})
        self.assertIn('&lt;script&gt;', result.json()['feedback'])
        self.assertNotIn('<script>test()', result.json()['feedback'])
        self.assertEqual(Practice.objects.get().python_code, code)

    def test_no_js_post_keeps_catalog_filters_anchor_and_latest_result(self):
        response = self.submit(ajax=False)
        self.assertRedirects(response, f'/catalog/?number=1&page=1&q=#task-{self.task.pk}', fetch_redirect_response=False)
        response = self.client.get(response.url)
        self.assertContains(response, '✓ Верно!')
        self.assertContains(response, 'SECRET_SOLUTION')

    def test_history_is_limited_to_five_recent_attempts(self):
        for i in range(8):
            Practice.objects.create(task=self.task, user=self.user, answer=f'ATTEMPT_{i}', correct=False)
        response = self.client.get('/catalog/?number=1')
        self.assertNotContains(response, '<code>ATTEMPT_0</code>')
        self.assertContains(response, '<code>ATTEMPT_7</code>')
