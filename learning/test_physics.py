import io
import tempfile
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from learning.models import Practice, Task, Topic, Variant
from learning.notebook_models import Notebook
from learning.security import form_token
from learning.services import physics_points, test_score
from learning.subjects import using


class PhysicsPlatformTests(TestCase):
    """Physics is a second subject: same accounts, own catalogue; informatics stays as it was."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        override = self.settings(MEDIA_ROOT=Path(temp.name))
        override.enable()
        self.addCleanup(override.disable)
        users = get_user_model().objects
        self.admin = users.create_user('teacher', is_staff=True, is_superuser=True)
        self.pupil = users.create_user('pupil')
        self.inf_topic = Topic.objects.create(number=1, title='Анализ информационных моделей')
        self.inf_task = Task.objects.create(topic=self.inf_topic, title='inf', statement='x', answer='5')
        call_command('seed_physics', stdout=io.StringIO())

    def switch(self, code):
        return self.client.post('/subject/', {'subject': code, 'next': '/catalog/?number=1'})

    def test_seed_is_repeatable_and_separate(self):
        out = io.StringIO()
        call_command('seed_physics', stdout=out)
        self.assertIn('tasks created 0', out.getvalue())
        self.assertEqual(Topic.objects.count(), 1)  # informatics by default
        with using('phys'):
            self.assertEqual(Topic.objects.count(), 26)
            self.assertGreater(Task.objects.count(), 150)
            self.assertEqual(Variant.objects.count(), 3)
            self.assertEqual(Notebook.objects.count(), 6)
            self.assertTrue(Task.objects.filter(content_blocks__kind='image').exists())
        self.assertEqual(Task.objects.count(), 1)

    def test_switch_keeps_the_section_and_scopes_pages(self):
        self.client.force_login(self.pupil)
        self.assertContains(self.client.get('/catalog/'), 'Найдено: 1')
        self.assertRedirects(self.switch('phys'), '/catalog/', fetch_redirect_response=False)
        page = self.client.get('/catalog/')
        self.assertContains(page, 'Физика · задача 1.1')
        self.assertNotContains(page, f'id="task-{self.inf_task.pk}"')
        self.assertContains(page, 'class="active" aria-pressed="true"><span aria-hidden="true">⚛</span> Физика')
        self.assertContains(self.client.get('/'), '26 шагов к экзамену')
        self.assertContains(self.client.get('/variants/'), 'Тренировочный вариант № 1 (физика, Инфокод)')
        self.switch('inf')
        self.assertContains(self.client.get('/'), '27 шагов к экзамену')
        self.assertEqual(self.client.post('/subject/', {'subject': 'chem'}).status_code, 404)

    def test_physics_answers_follow_exam_rules(self):
        self.assertEqual(physics_points(5, '41', '14'), 2)       # any order
        self.assertEqual(physics_points(5, '134', '14'), 1)      # one extra digit
        self.assertEqual(physics_points(6, '21', '12'), 0)       # order matters
        self.assertEqual(physics_points(6, '22', '12'), 1)       # one wrong digit
        self.assertEqual(physics_points(1, '2.5', '2,5'), 1)
        self.assertEqual(physics_points(1, '2,50', '2,5'), 1)
        self.assertEqual(physics_points(19, '1,8 0,2', '1,8 0,2'), 1)
        self.assertEqual(physics_points(20, '21', '12'), 1)
        self.assertEqual(physics_points(26, '1,2', '1,2'), 4)
        self.assertEqual(test_score(45, 'phys'), 100)
        self.assertEqual(test_score(9, 'phys'), 39)
        self.client.force_login(self.pupil)
        self.switch('phys')
        with using('phys'):
            task = Task.objects.filter(topic__number=5).first()
        reversed_answer = task.answer[::-1]
        reply = self.client.post('/catalog/', {'task_id': task.pk, 'submit_token': form_token(self.pupil, f'task:{task.pk}'),
                                 f'task-{task.pk}-answer': reversed_answer}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertTrue(reply.json()['solved'])
        self.assertTrue(Practice.objects.get(task=task).correct)
