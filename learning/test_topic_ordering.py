from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import Practice, Task, Topic
from .views import topic_stats


class TopicOrderingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student = get_user_model().objects.create_user('topic-order-student')
        cls.teacher = get_user_model().objects.create_superuser('topic-order-teacher')
        # Match a database whose primary-key/insertion order differs from EGE numbers.
        for number in (22, 11, 9, 15, 26, 19, 21, 3, 17, 5, 4, 10, 6, 14, 13, 2, 16, 7, 12, 24, 25, 20, 1, 18, 27, 23, 8):
            Topic.objects.create(number=number, title=f'Номер {number}')

    def test_grouped_topic_query_explicitly_orders_numbers(self):
        with CaptureQueriesContext(connection) as queries:
            topics, solved = topic_stats(self.student)
        grouped = [query['sql'] for query in queries if 'GROUP BY' in query['sql'] and 'learning_topic' in query['sql']]
        self.assertEqual(len(grouped), 1)
        self.assertIn('ORDER BY', grouped[0].upper())
        self.assertEqual([topic.number for topic in topics], list(range(1, 28)))
        self.assertEqual(solved, set())

    def test_dashboard_and_catalog_are_numeric_for_students_and_teachers(self):
        for user in (self.student, self.teacher):
            self.client.force_login(user)
            for route in ('dashboard', 'catalog'):
                with self.subTest(user=user.username, route=route):
                    response = self.client.get(reverse(route))
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual([topic.number for topic in response.context['topics']], list(range(1, 28)))

    def test_sorting_keeps_task_links_and_progress(self):
        topic = Topic.objects.get(number=9)
        task = Task.objects.create(topic=topic, title='Существующее задание', answer='42')
        Task.objects.create(topic=topic, title='Черновик', answer='1', published=False)
        Practice.objects.create(user=self.student, task=task, answer='42', correct=True)
        before = dict(Topic.objects.values_list('pk', 'number'))
        topics, solved = topic_stats(self.student)
        displayed = next(item for item in topics if item.number == 9)
        self.assertEqual((displayed.pk, displayed.total, displayed.solved, displayed.percent), (topic.pk, 1, 1, 100))
        self.assertEqual(solved, {task.pk})
        task.refresh_from_db()
        self.assertEqual(task.topic_id, topic.pk)
        self.assertEqual(dict(Topic.objects.values_list('pk', 'number')), before)
