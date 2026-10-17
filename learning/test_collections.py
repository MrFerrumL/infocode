from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from learning.collection_models import Collection
from learning.collection_topics import plan_of, topic
from learning.collections_builder import clean_title, parse_title, sync_all
from learning.models import Practice, Task, Topic
from learning.security import form_token


class CollectionsTests(TestCase):
    """Collections named like Yandex Uchebnik's, filled with KOMPEGE and Polyakov tasks."""

    def setUp(self):
        topic8 = Topic.objects.create(number=8, title='Комбинаторика')
        make = lambda i, **kw: Task.objects.create(topic=topic8, title=f't{i}', statement='x', answer=str(i), source_system='kompege', external_id=i, **kw)
        self.easy = [make(i, difficulty='easy') for i in range(1, 4)]
        self.hard = [make(i, difficulty='hard') for i in range(10, 12)]
        self.listed = make(30, difficulty='medium')
        self.exam = make(20, difficulty='easy', source_kind='exam')
        self.user = get_user_model().objects.create_user('pupil')
        self.client.force_login(self.user)
        # Number 8 in the Yandex list: three combinatorics sets and an ordered-list set.
        from learning import collections_builder
        names = [row for row in collections_builder.load_names() if row[0].startswith('№ 8')]
        for target, value in (('load_names', names), ('load_topics', {'kompege': {'8': {'30': 'list'}}})):
            patcher = patch(f'learning.collections_builder.{target}', return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_titles_topics_and_modes(self):
        self.assertEqual(parse_title('№ 19-21 — повторение с одной кучей'), (19, 'повторение с одной кучей'))
        self.assertEqual(clean_title('№ 12 — знакомство от Ильи Карпачёва'), '№ 12 — знакомство')
        self.assertEqual(clean_title('№15 — решаем кодом'), '№ 15 — решаем кодом')
        self.assertEqual(plan_of(5, 'двоичная система счисления'), ('binary', 'any'))
        self.assertEqual(plan_of(19, 'повторение с двумя кучами'), ('two', 'exam'))
        self.assertEqual(plan_of(7, 'аудио, повторение'), ('audio', 'exam'))
        self.assertEqual(topic(19, 'Перед игроками лежат две кучи камней'), 'two')
        self.assertEqual(topic(15, 'истинно при любых целых x и y'), 'graph')

    def test_collections_follow_yandex_names_and_topics(self):
        removed, built = sync_all(size=10)
        titles = list(Collection.objects.filter(number=8).values_list('title', flat=True))
        self.assertIn('№ 8 — упорядоченный список', titles)
        self.assertIn('№ 8 — комбинаторика, знакомство', titles)
        listed = Collection.objects.get(title='№ 8 — упорядоченный список')
        self.assertEqual(list(listed.items.values_list('task_id', flat=True)), [self.listed.pk])
        intro = Collection.objects.get(title='№ 8 — комбинаторика, знакомство')
        self.assertNotIn(self.listed.pk, intro.items.values_list('task_id', flat=True))
        self.assertFalse(Collection.objects.filter(source='yandex').exists())
        self.assertFalse(Collection.objects.exclude(url='').exists())
        everything = [pk for c in Collection.objects.filter(number=8) for pk in c.items.values_list('task_id', flat=True)]
        self.assertEqual(len(everything), len(set(everything)))

    def test_list_has_no_external_links_and_tasks_are_solved_inside(self):
        sync_all(size=10)
        page = self.client.get('/collections/')
        self.assertNotContains(page, 'yandex')
        self.assertContains(page, 'Упорядоченный список')
        collection = Collection.objects.get(title='№ 8 — упорядоченный список')
        task = self.listed
        detail = self.client.get(f'/collections/{collection.pk}/')
        self.assertContains(detail, f'id="task-{task.pk}"')
        reply = self.client.post(f'/collections/{collection.pk}/', {'task_id': task.pk, 'submit_token': form_token(self.user, f'task:{task.pk}'),
                                 f'task-{task.pk}-answer': task.answer}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertTrue(reply.json()['solved'])
        self.assertTrue(Practice.objects.filter(user=self.user, task=task, correct=True).exists())
        self.assertContains(self.client.get('/collections/'), '1/1')

    def test_old_yandex_links_are_removed(self):
        Collection.objects.create(key='yandex:x', source='yandex', number=8, title='№ 8 — x', url='https://education.yandex.ru/x')
        removed, _ = sync_all(size=10)
        self.assertEqual(removed, 1)
