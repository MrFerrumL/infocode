import io

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .models import Course, Task, Topic, Variant


class EmptyPlatformInitializationTests(TestCase):
    def test_empty_installation_can_add_tasks_without_demo_content(self):
        call_command('init_platform', stdout=io.StringIO())

        self.assertEqual(list(Topic.objects.values_list('number', flat=True)), list(range(1, 28)))
        for model in (get_user_model(), Task, Course, Variant):
            self.assertEqual(model.objects.count(), 0)

        teacher = get_user_model().objects.create_superuser('owner', password='SetupTestPassword123!')
        self.client.force_login(teacher)
        response = self.client.get(reverse('task_create'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form'].fields['topic'].queryset.count(), 27)
        response = self.client.post(reverse('task_create'), {
            'topic': Topic.objects.get(number=27).pk,
            'title': 'Моя первая задача',
            'statement': 'Найдите результат программы.',
            'difficulty': 'ege',
            'answer': '42',
            'published': 'on',
        })
        self.assertEqual(response.status_code, 302)
        task = Task.objects.get()
        self.assertEqual(task.topic.number, 27)
        self.assertTrue(task.published)

    def test_repeated_initialization_preserves_existing_topics_and_tasks(self):
        topic = Topic.objects.create(number=1, title='Мои графы', description='Описание преподавателя')
        task = Task.objects.create(topic=topic, title='Существующая задача', statement='Условие', answer='7')

        call_command('init_platform', stdout=io.StringIO())
        heading_ids = list(Topic.objects.values_list('pk', flat=True))
        call_command('init_platform', stdout=io.StringIO())

        topic.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(topic.title, 'Мои графы')
        self.assertEqual(topic.description, 'Описание преподавателя')
        self.assertEqual(list(Topic.objects.values_list('pk', flat=True)), heading_ids)
        self.assertEqual(Task.objects.count(), 1)
        self.assertEqual(task.answer, '7')
