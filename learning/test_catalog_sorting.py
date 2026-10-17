from django.contrib.auth import get_user_model
from django.test import TestCase
from learning.models import Task, Topic


class CatalogSortingTests(TestCase):
    """KOMPEGE lists the newest source tasks first; the catalogue follows it."""

    def setUp(self):
        topic = Topic.objects.create(number=8, title='Комбинаторика')
        self.own = Task.objects.create(topic=topic, title='own', statement='x', answer='1', difficulty='hard')
        self.old = Task.objects.create(topic=topic, title='old', statement='x', answer='1', source_system='kompege', external_id=164, difficulty='easy')
        self.demo = Task.objects.create(topic=topic, title='demo', statement='x', answer='1', source_system='kompege', external_id=31505, difficulty='medium')
        self.client.force_login(get_user_model().objects.create_user('pupil'))

    def order(self, sort=''):
        page = self.client.get('/catalog/', {'number': 8, 'sort': sort})
        return [task.title for task in page.context['page']]

    def test_newest_source_tasks_first_and_own_tasks_after(self):
        self.assertEqual(self.order(), ['demo', 'old', 'own'])

    def test_other_sort_orders(self):
        self.assertEqual(self.order('old'), ['old', 'demo', 'own'])
        self.assertEqual(self.order('easy'), ['old', 'demo', 'own'])
        self.assertEqual(self.order('hard'), ['own', 'demo', 'old'])
