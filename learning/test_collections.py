from django.contrib.auth import get_user_model
from django.test import TestCase
from learning.collection_models import Collection
from learning.collections_builder import parse_yandex, sync_all
from learning.models import Practice, Task, Topic
from learning.security import form_token


class CollectionsTests(TestCase):
    """Collections: own sets built from the catalogue and Yandex Uchebnik sets as links."""

    def setUp(self):
        topic = Topic.objects.create(number=8, title='Комбинаторика')
        make = lambda i, **kw: Task.objects.create(topic=topic, title=f't{i}', statement='x', answer=str(i), source_system='kompege', external_id=i, **kw)
        self.easy = [make(i, difficulty='easy') for i in range(1, 4)]
        self.hard = [make(i, difficulty='hard') for i in range(10, 12)]
        self.exam = make(20, difficulty='easy', source_kind='exam')
        self.user = get_user_model().objects.create_user('pupil')
        self.client.force_login(self.user)

    def test_titles_of_yandex_collections_are_parsed(self):
        self.assertEqual(parse_yandex('№ 19-21 — повторение с одной кучей'), (19, 'повторение с одной кучей'))
        self.assertEqual(parse_yandex('№15 — решаем кодом'), (15, 'решаем кодом'))
        self.assertEqual(parse_yandex('№ 26 — все официальные задания'), (26, 'все официальные задания'))

    def test_sync_builds_links_and_own_sets_without_overlap(self):
        links, own = sync_all(size=10)
        self.assertEqual(links, 73)
        self.assertEqual(own, 3)
        sets = {c.level: list(c.items.values_list('task_id', flat=True)) for c in Collection.objects.filter(source='infocode')}
        self.assertEqual(sets['повторение'], [self.exam.pk])
        self.assertEqual(sorted(sets['знакомство']), sorted(t.pk for t in self.easy))
        self.assertEqual(sorted(sets['на подумать']), sorted(t.pk for t in self.hard))
        self.assertTrue(Collection.objects.get(key__startswith='yandex:', title='№ 17 — знакомство').url.startswith('https://education.yandex.ru/'))
        sync_all(size=10)
        self.assertEqual(Collection.objects.count(), 76)

    def test_list_and_answering_inside_a_collection(self):
        sync_all(size=10)
        page = self.client.get('/collections/')
        self.assertContains(page, 'Подборки Инфокода')
        self.assertContains(page, 'Яндекс Учебник')
        self.assertContains(page, 'target="_blank"')
        intro = Collection.objects.get(source='infocode', level='знакомство')
        task = self.easy[0]
        detail = self.client.get(f'/collections/{intro.pk}/')
        self.assertContains(detail, f'id="task-{task.pk}"')
        self.assertContains(detail, 'Решено 0 из 3')
        reply = self.client.post(f'/collections/{intro.pk}/', {'task_id': task.pk, 'submit_token': form_token(self.user, f'task:{task.pk}'),
                                 f'task-{task.pk}-answer': task.answer}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertTrue(reply.json()['solved'])
        self.assertTrue(Practice.objects.filter(user=self.user, task=task, correct=True).exists())
        self.assertContains(self.client.get('/collections/'), '1/3')

    def test_external_collections_have_no_local_page(self):
        sync_all(size=10)
        link = Collection.objects.filter(source='yandex').first()
        self.assertEqual(self.client.get(f'/collections/{link.pk}/').status_code, 404)
