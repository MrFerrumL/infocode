from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import Practice, Task, Topic
from .security import form_token


class AnswerCellTests(TestCase):
    """№ 17, 26 and 27 take several numbers: the catalogue gives one cell per number and checks them in order."""

    def setUp(self):
        self.user = get_user_model().objects.create_user('pupil', password='x')
        self.client.force_login(self.user)
        self.t27 = Task.objects.create(topic=Topic.objects.create(number=27, title='Кластеры'), title='27', statement='Условие',
                                       answer='700 600 8100 9200', published=True, in_catalog=True)
        self.t26 = Task.objects.create(topic=Topic.objects.create(number=26, title='Сортировка'), title='26', statement='Условие',
                                       answer='4 120', published=True, in_catalog=True)
        self.t8 = Task.objects.create(topic=Topic.objects.create(number=8, title='Комбинаторика'), title='8', statement='Условие',
                                      answer='42', published=True, in_catalog=True)

    def send(self, task, cells):
        data = {'task_id': str(task.pk), 'submit_token': form_token(self.user, f'task:{task.pk}')}
        data.update({f'task-{task.pk}-cell-{k // 2}-{k % 2}': value for k, value in enumerate(cells)})
        return self.client.post('/catalog/', data, HTTP_X_REQUESTED_WITH='XMLHttpRequest').json()

    def test_cells_are_shown_only_where_needed(self):
        page = self.client.get('/catalog/?number=27')
        self.assertContains(page, f'name="task-{self.t27.pk}-cell-1-1"')
        self.assertContains(page, 'Файл B')
        self.assertNotContains(page, f'name="task-{self.t27.pk}-answer"')
        self.assertContains(self.client.get('/catalog/?number=26'), f'name="task-{self.t26.pk}-cell-0-1"')
        plain = self.client.get('/catalog/?number=8')
        self.assertContains(plain, f'name="task-{self.t8.pk}-answer"')
        self.assertNotContains(plain, 'cell-0-0')

    def test_cells_are_checked_in_order(self):
        self.assertFalse(self.send(self.t27, ['600', '700', '8100', '9200'])['solved'])
        self.assertTrue(self.send(self.t27, ['700', ' 600 ', '8100', '9200'])['solved'])
        self.assertEqual(Practice.objects.filter(task=self.t27).first().answer, '700 600 8100 9200')
        self.assertTrue(self.send(self.t26, ['4', '120'])['solved'])
        self.assertIn('answer', self.send(self.t26, ['', ''])['errors'])  # empty cells are not an attempt
