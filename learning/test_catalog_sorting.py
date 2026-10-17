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

    def test_polyakov_tasks_follow_his_numbering(self):
        topic = Topic.objects.get(number=8)
        for n in (3, 1, 2):
            Task.objects.create(topic=topic, title=f'p{n}', statement='x', answer='1', source_system='polyakov', external_id=n)
        self.assertEqual(self.order(), ['demo', 'old', 'p1', 'p2', 'p3', 'own'])
        self.assertEqual(self.order('desc'), ['demo', 'old', 'p3', 'p2', 'p1', 'own'])
        page = self.client.get('/catalog/', {'number': 8, 'site': 'polyakov'})
        self.assertEqual([t.title for t in page.context['page']], ['p1', 'p2', 'p3'])

    def test_other_sort_orders(self):
        self.assertEqual(self.order('asc'), ['old', 'demo', 'own'])
        self.assertEqual(self.order('old'), ['old', 'demo', 'own'])
        self.assertEqual(self.order('easy'), ['old', 'demo', 'own'])
        self.assertEqual(self.order('hard'), ['own', 'demo', 'old'])
