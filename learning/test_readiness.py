import io
import tempfile
from unittest.mock import patch
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command, CommandError
from django.db import DatabaseError
from django.test import TestCase
from django.urls import reverse
from .models import Topic, Task, TaskBlock, Variant, VariantItem, Course, Section, Lesson
from .platform_views import archive
from .readiness import content_report


class ReadinessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user('check-teacher', is_staff=True)
        cls.admin = User.objects.create_user('check-admin', is_staff=True, is_superuser=True)
        cls.student = User.objects.create_user('check-student')
        cls.topic = Topic.objects.create(number=1, title='Графы')
        cls.task = Task.objects.create(topic=cls.topic, title='Задание', statement='Условие', answer='42')

    def test_checks_archived_and_snapshot_files_without_changing_data(self):
        with tempfile.TemporaryDirectory() as directory, self.settings(MEDIA_ROOT=directory):
            block = TaskBlock.objects.create(task=self.task, kind='file', file=SimpleUploadedFile('data.txt', b'42'))
            archive(self.teacher, self.task)
            variant = Variant.objects.create(creator=self.teacher, title='Выданный вариант')
            VariantItem.objects.create(variant=variant, task=self.task, position=1, number=1, statement='Условие', answer='42', solution='', blocks=[{'file': block.file.name}, {'file': 'missing-in-snapshot.txt'}])
            report = content_report()
            self.assertTrue(report['has_errors'])
            self.assertEqual(report['missing_count'], 1)
            self.assertEqual(report['missing'][0]['name'], 'missing-in-snapshot.txt')
            self.assertEqual(report['checked_files'], 2)
            self.assertFalse(Task.objects.filter(pk=self.task.pk).exists())
            self.assertTrue(block.file.storage.exists(block.file.name))
            with self.assertRaises(CommandError):
                call_command('check_content', strict=True, stdout=io.StringIO())
            block.file.delete(save=False)
            self.assertEqual(content_report()['missing_count'], 2)

    def test_empty_published_content_is_reported_and_drafts_are_allowed(self):
        Task.objects.create(topic=self.topic, title='Пустое опубликованное', answer='1', published=True)
        Task.objects.create(topic=self.topic, title='Пустой черновик', answer='1', published=False)
        course = Course.objects.create(title='Курс', owner=self.teacher, published=True)
        section = Section.objects.create(title='Раздел', course=course)
        Lesson.objects.create(title='Пустой урок', section=section, published=True)
        with tempfile.TemporaryDirectory() as directory, self.settings(MEDIA_ROOT=directory):
            report = content_report()
        rows = {item['title']: item for item in report['checks']}
        self.assertEqual(rows['Опубликованные задания']['level'], 'warning')
        self.assertIn('Без условия: 1.', rows['Опубликованные задания']['detail'])
        self.assertEqual(rows['Опубликованные уроки']['level'], 'warning')
        self.assertFalse(report['has_errors'])

    def test_only_administrator_can_see_platform_report(self):
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(reverse('platform_check')).status_code, 403)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse('platform_check')).status_code, 302)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('platform_check')), 'Проверка платформы')
        self.assertEqual(self.client.post(reverse('platform_check'), {}).status_code, 405)

    def test_database_error_does_not_expose_connection_details(self):
        with patch('learning.readiness.connection.cursor', side_effect=DatabaseError('private connection details')):
            report = content_report()
        self.assertTrue(report['has_errors'])
        self.assertNotIn('private connection details', str(report))
