import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.db.models import QuerySet
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from . import game_groups
from .models import Practice, Task, Topic
from .static_storage import HashedStaticStorage
from .views import topic_stats


class FoldFollowUpsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        topics = {n: Topic.objects.create(number=n, title=f'№ {n}') for n in (19, 20, 21)}
        make = lambda n, ident: Task.objects.create(topic=topics[n], title=f'{n}-{ident}', answer='1', published=True, in_catalog=True,  # noqa: E731
                                                    source_system='kompege', external_id=ident)
        cls.group = [make(19, 7), make(20, 7), make(21, 7)]
        cls.alone = make(20, 8)  # a 20 without a 19 partner stays a card of its own

    def test_a_group_is_one_card_and_the_ids_are_a_subquery(self):
        every = Task.objects.filter(published=True, in_catalog=True)
        with patch.object(game_groups, 'active', return_value=True):
            ids = game_groups.fold_follow_ups(every)
            self.assertIsInstance(ids, QuerySet)  # never a list of every id of the catalogue
            self.assertEqual(set(ids.values_list('pk', flat=True)), {self.group[0].pk, self.alone.pk})
            only_follow_up = game_groups.fold_follow_ups(every.filter(topic__number=21))
            self.assertEqual(set(only_follow_up.values_list('pk', flat=True)), {self.group[0].pk})  # the 21 shows as its 19

    def test_without_groups_everything_stays(self):
        every = Task.objects.filter(published=True, in_catalog=True)
        with patch.object(game_groups, 'active', return_value=False):
            self.assertEqual(set(game_groups.fold_follow_ups(every).values_list('pk', flat=True)), {t.pk for t in [*self.group, self.alone]})


class TopicProgressTests(TestCase):
    def test_solved_are_counted_once_per_task_and_only_in_the_catalogue(self):
        student = get_user_model().objects.create_user('pupil')
        topic = Topic.objects.create(number=5, title='№ 5')
        shown, hidden = (Task.objects.create(topic=topic, title=t, answer='1', published=p, in_catalog=p) for t, p in (('a', True), ('b', False)))
        for task in (shown, shown, hidden):  # two right answers to one task do not count twice
            Practice.objects.create(user=student, task=task, answer='1', correct=True)
        with CaptureQueriesContext(connection) as queries:
            topics, solved = topic_stats(student)
        row = next(t for t in topics if t.number == 5)
        self.assertEqual((row.total, row.solved, row.percent), (1, 1, 100))
        self.assertEqual(solved, {shown.pk, hidden.pk})
        self.assertLessEqual(len(queries), 3)  # no load of every task of the catalogue


class DuplicatesCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client.force_login(get_user_model().objects.create_user('teacher', password='x', is_staff=True, is_superuser=True))

    def test_the_search_runs_once_in_ten_minutes_unless_asked_again(self):
        with patch('learning.platform_views.duplicate_groups', return_value=[]) as search:
            for url in ('/teacher/duplicates/', '/teacher/duplicates/'):
                self.assertEqual(self.client.get(url).status_code, 200)
            self.assertEqual(search.call_count, 1)
            self.client.get('/teacher/duplicates/?fresh=1')
            self.assertEqual(search.call_count, 2)


class HashedStaticStorageTests(SimpleTestCase):
    def test_names_come_from_the_manifest_and_a_missing_file_keeps_its_plain_name(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'staticfiles.json').write_text(json.dumps({'version': '1.1', 'paths': {'app.css': 'app.0123456789ab.css'}, 'hash': 'x'}), encoding='utf-8')
            storage = HashedStaticStorage(location=root, base_url='/static/')
            self.assertEqual(storage.url('app.css'), '/static/app.0123456789ab.css')
            self.assertEqual(storage.url('nothing-here.js'), '/static/nothing-here.js')  # no error, the page still renders
