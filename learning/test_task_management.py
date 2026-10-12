import tempfile
from datetime import timedelta
from pathlib import Path
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from .models import (Course, Section, Lesson, Step, StepProgress, StepSubmission,
                     Task, TaskBlock, Topic, TrashEntry, Practice, ExamAttempt, ExamAnswer)
from .platform_views import archive
from .services import create_variant, finish_attempt


class TaskManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user('catalogue-teacher', is_staff=True)
        cls.admin = User.objects.create_user('catalogue-admin', is_staff=True, is_superuser=True)
        cls.student = User.objects.create_user('catalogue-student')
        cls.topic = Topic.objects.create(number=6, title='Алгоритмы')
        cls.other_topic = Topic.objects.create(number=1, title='Графы')
        cls.task = Task.objects.create(topic=cls.topic, title='Задача с историей', statement='Вычислите 6 × 7.', answer='42', code_mode='optional')
        cls.other = Task.objects.create(topic=cls.other_topic, title='Вторая задача', statement='Вычислите 2 × 3.', answer='6')

    def setUp(self):
        self.client.force_login(self.teacher)

    def test_confirmation_is_required_and_archive_preserves_answers_code_files_and_snapshot(self):
        with tempfile.TemporaryDirectory() as directory, self.settings(MEDIA_ROOT=directory):
            block = TaskBlock.objects.create(task=self.task, kind='file', file=SimpleUploadedFile('numbers.txt', b'6 7'))
            variant = create_variant(creator=self.teacher, title='Выданная КР', topics=[self.topic], published=True)
            item = variant.items.get()
            attempt = ExamAttempt.objects.create(user=self.student, variant=variant, deadline=timezone.now()+timedelta(hours=1))
            answer = ExamAnswer.objects.create(attempt=attempt, item=item, answer='42', python_code='print(6 * 7)')
            finish_attempt(attempt)
            practice = Practice.objects.create(user=self.student, task=self.task, answer='42', correct=True, python_code='print(42)')
            url = reverse('task_delete', args=[self.task.pk])
            self.assertContains(self.client.get(url), 'Удалить в корзину')
            self.assertEqual(self.client.post(url, {}).status_code, 200)
            self.assertTrue(Task.objects.filter(pk=self.task.pk).exists())
            self.assertRedirects(self.client.post(url, {'confirm': 'yes'}), reverse('teacher_tasks'))
            self.assertFalse(Task.objects.filter(pk=self.task.pk).exists())
            item.refresh_from_db()
            answer.refresh_from_db()
            practice.refresh_from_db()
            attempt.refresh_from_db()
            self.assertEqual(item.statement, 'Вычислите 6 × 7.')
            self.assertEqual(item.blocks[0]['file'], block.file.name)
            self.assertTrue(Path(block.file.path).exists())
            self.assertEqual(answer.python_code, 'print(6 * 7)')
            self.assertEqual(practice.python_code, 'print(42)')
            self.assertEqual(attempt.score, 1)
            self.client.force_login(self.student)
            self.assertEqual(self.client.get(reverse('task', args=[self.task.pk])).status_code, 404)
            self.assertEqual(self.client.get(reverse('result', args=[attempt.pk])).status_code, 200)

    def test_bulk_deletion_confirms_exact_selection_and_preserves_filters(self):
        return_to = reverse('teacher_tasks')+'?topic=6&status=published&page=1'
        data = {'action': 'archive', 'selected': [self.task.pk], 'return_to': return_to}
        page = self.client.post(reverse('bulk_tasks'), data)
        self.assertEqual(page.status_code, 200)
        self.assertTrue(Task.objects.filter(pk=self.task.pk).exists())
        self.assertNotContains(page, self.other.title)
        response = self.client.post(reverse('bulk_tasks'), {**data, 'confirm': 'yes'})
        self.assertEqual(response['Location'], return_to)
        self.assertTrue(Task.objects.filter(pk=self.other.pk).exists())

    def test_invalid_bulk_actions_and_empty_selection_do_not_change_tasks(self):
        for data in ({'action': 'unknown', 'selected': [self.task.pk]},
                     {'action': 'difficulty', 'difficulty': 'unknown', 'selected': [self.task.pk]},
                     {'action': 'archive', 'confirm': 'yes', 'selected': []}):
            with self.subTest(data=data):
                self.assertEqual(self.client.post(reverse('bulk_tasks'), data).status_code, 302)
                self.assertTrue(Task.objects.filter(pk=self.task.pk, published=True, difficulty='easy').exists())
        self.assertFalse(TrashEntry.objects.exists())

    def test_delete_requires_teacher_and_csrf_and_rejects_external_redirect(self):
        url = reverse('task_delete', args=[self.task.pk])
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(url, {'confirm': 'yes'}).status_code, 302)
        self.assertTrue(Task.objects.filter(pk=self.task.pk).exists())
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.teacher)
        self.assertEqual(csrf_client.post(url, {'confirm': 'yes'}).status_code, 403)
        self.client.force_login(self.teacher)
        response = self.client.post(url, {'confirm': 'yes', 'return_to': 'https://untrusted.example/teacher/tasks/'})
        self.assertEqual(response['Location'], reverse('teacher_tasks'))

    def test_repeated_archive_does_not_duplicate_trash_or_overwrite_concurrent_edit(self):
        stale = Task.objects.get(pk=self.task.pk)
        Task.objects.filter(pk=self.task.pk).update(title='Последнее сохранённое название')
        self.assertTrue(archive(self.teacher, stale))
        self.assertFalse(archive(self.teacher, self.task))
        self.assertEqual(TrashEntry.objects.filter(model='learning.task', object_id=self.task.pk).count(), 1)
        self.assertEqual(Task.all_objects.get(pk=self.task.pk).title, 'Последнее сохранённое название')
        self.assertEqual(self.client.post(reverse('task_delete', args=[self.task.pk]), {'confirm': 'yes'}).status_code, 302)

    def test_django_admin_single_delete_of_used_task_archives_without_protect_error(self):
        variant = create_variant(creator=self.teacher, title='КР с задачей', topics=[self.topic])
        practice = Practice.objects.create(user=self.student, task=self.task, answer='42', correct=True)
        self.client.force_login(self.admin)
        url = reverse('admin:learning_task_delete', args=[self.task.pk])
        page = self.client.get(url)
        self.assertContains(page, 'Переместить в корзину?')
        self.assertEqual(page.context['protected'], [])
        self.assertEqual(self.client.post(url, {'post': 'yes'}).status_code, 302)
        self.assertTrue(Practice.objects.filter(pk=practice.pk).exists())
        self.assertEqual(variant.items.get().statement, self.task.statement)
        self.assertFalse(Task.objects.filter(pk=self.task.pk).exists())

    def test_django_admin_bulk_delete_confirms_and_keeps_all_related_records(self):
        variant = create_variant(creator=self.teacher, title='КР с задачей', topics=[self.topic])
        self.client.force_login(self.admin)
        url = reverse('admin:learning_task_changelist')
        data = {'action': 'delete_selected', '_selected_action': [self.task.pk, self.other.pk], 'select_across': '0'}
        self.assertContains(self.client.post(url, data), 'Переместить в корзину?')
        self.assertTrue(Task.objects.filter(pk=self.task.pk).exists())
        self.assertEqual(self.client.post(url, {**data, 'post': 'yes'}).status_code, 302)
        self.assertEqual(Task.objects.count(), 0)
        self.assertEqual(variant.items.count(), 1)
        self.assertEqual(TrashEntry.objects.count(), 2)

    def test_course_archive_in_admin_keeps_progress_and_step_submissions(self):
        course = Course.objects.create(title='Курс с историей', owner=self.teacher, published=True)
        section = Section.objects.create(title='Раздел', course=course)
        lesson = Lesson.objects.create(title='Урок', section=section, published=True)
        step = Step.objects.create(title='Теория', lesson=lesson, body='Материал', published=True)
        progress = StepProgress.objects.create(user=self.student, step=step, completed=True)
        submission = StepSubmission.objects.create(user=self.student, step=step, answer='Верно', correct=True)
        self.client.force_login(self.admin)
        url = reverse('admin:learning_course_delete', args=[course.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, {'post': 'yes'}).status_code, 302)
        self.assertTrue(StepProgress.objects.filter(pk=progress.pk).exists())
        self.assertTrue(StepSubmission.objects.filter(pk=submission.pk).exists())
        self.assertFalse(Step.objects.filter(pk=step.pk).exists())
        self.assertTrue(Step.all_objects.filter(pk=step.pk).exists())

    def test_catalogue_filters_and_pagination_keep_ege_number(self):
        Task.objects.bulk_create([Task(topic=self.topic, title=f'Алгоритм {i}', statement='Условие', answer='1') for i in range(22)])
        page = self.client.get(reverse('teacher_tasks'), {'topic': '6', 'status': 'published'})
        self.assertEqual(page.context['page'].paginator.count, 23)
        self.assertNotContains(page, self.other.title)
        self.assertContains(page, 'topic=6&amp;status=published&amp;page=2')
        self.assertTrue(all(task.topic_id == self.topic.pk for task in self.client.get(reverse('teacher_tasks'), {'topic': '6', 'page': '2'}).context['page']))

    def test_trash_search_restore_as_draft_and_teacher_isolation(self):
        archive(self.teacher, self.task)
        archive(self.admin, self.other)
        page = self.client.get(reverse('trash'), {'kind': 'learning.task', 'q': 'историей'})
        self.assertContains(page, self.task.title)
        self.assertNotContains(page, self.other.title)
        entry = TrashEntry.objects.get(object_id=self.task.pk, model='learning.task')
        self.assertEqual(self.client.post(reverse('trash'), {'pk': entry.pk}).status_code, 302)
        self.assertFalse(Task.objects.get(pk=self.task.pk).published)
        foreign_entry = TrashEntry.objects.get(object_id=self.other.pk, model='learning.task')
        self.assertEqual(self.client.post(reverse('trash'), {'pk': foreign_entry.pk}).status_code, 404)
