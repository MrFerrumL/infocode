from datetime import timedelta

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import (AssignmentRecipient, Course, ExamAnswer, ExamAttempt, HomeworkSubmission,
                     LearningAssignment, Lesson, Section, Step, StudentAssignment, Task, Topic, Variant, VariantItem)


class WorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user('workspace-author', is_staff=True)
        cls.other = User.objects.create_user('workspace-other', is_staff=True)
        cls.student = User.objects.create_user('workspace-student')
        StudentAssignment.objects.create(teacher=cls.teacher, student=cls.student)
        cls.course = Course.objects.create(title='Мой черновик', owner=cls.teacher)
        Course.objects.create(title='Чужой черновик', owner=cls.other)
        cls.section = Section.objects.create(course=cls.course, title='Раздел')
        cls.lesson = Lesson.objects.create(section=cls.section, title='Урок')
        cls.step = Step.objects.create(lesson=cls.lesson, title='Мой слайд', kind='text')
        now = timezone.now()
        cls.work = LearningAssignment.objects.create(teacher=cls.teacher, title='Моя ручная работа',
            target='manual', starts_at=now-timedelta(days=2), due_at=now-timedelta(days=1))
        cls.recipient = AssignmentRecipient.objects.create(assignment=cls.work, student=cls.student)
        cls.submission = HomeworkSubmission.objects.create(recipient=cls.recipient, text='Решение')
        foreign = LearningAssignment.objects.create(teacher=cls.other, title='Чужая ручная работа',
            target='manual', starts_at=now, due_at=now+timedelta(days=2))
        HomeworkSubmission.objects.create(recipient=AssignmentRecipient.objects.create(assignment=foreign, student=cls.student), text='Чужое решение')
        cls.variant = Variant.objects.create(creator=cls.teacher, title='Моя контрольная')
        Variant.objects.create(creator=cls.other, title='Чужой вариант')
        cls.kr = LearningAssignment.objects.create(teacher=cls.teacher, title='Моя КР', variant=cls.variant,
            starts_at=now-timedelta(days=1), due_at=now+timedelta(days=1))
        AssignmentRecipient.objects.create(assignment=cls.kr, student=cls.student)
        cls.attempt = ExamAttempt.objects.create(user=cls.student, variant=cls.variant, deadline=now, submitted_at=now)
        task = Task.objects.create(topic=Topic.objects.create(number=6, title='Алгоритмы'), title='Задача', answer='42')
        cls.item = VariantItem.objects.create(variant=cls.variant, task=task, position=1, number=6, statement='Задача', answer='42')
        ExamAnswer.objects.create(attempt=cls.attempt, item=cls.item, answer='42', python_code='print(42)', correct=True)

    def setUp(self):
        self.client.force_login(self.teacher)

    def test_home_uses_real_scoped_data_and_keeps_all_tools_accessible(self):
        page = self.client.get(reverse('teacher'))
        self.assertEqual(page.context['pending_count'], 1)
        self.assertEqual(page.context['student_count'], 1)
        self.assertEqual(page.context['course_count'], 1)
        self.assertContains(page, 'Мой черновик')
        self.assertNotContains(page, 'Чужой черновик')
        self.assertNotContains(page, 'Чужой вариант')
        self.assertEqual(list(page.context['overdue_students'])[0]['overdue_count'], 1)
        for name in ['analytics', 'teacher_groups', 'media_library', 'import_tasks', 'duplicates', 'trash', 'audit', 'dashboard']:
            self.assertContains(page, reverse(name))

    def test_review_queue_scopes_manual_and_python_and_requires_staff(self):
        page = self.client.get(reverse('review_queue'))
        self.assertContains(page, 'Моя ручная работа')
        self.assertNotContains(page, 'Чужая ручная работа')
        page = self.client.get(reverse('review_queue'), {'mode': 'python'})
        self.assertEqual(list(page.context['page']), [self.attempt])
        self.assertContains(page, reverse('result', args=[self.attempt.pk]))
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse('review_queue')).status_code, 302)

    def test_queue_search_pagination_and_reviewed_filter(self):
        HomeworkSubmission.objects.bulk_create([HomeworkSubmission(recipient=self.recipient, text=str(i)) for i in range(27)])
        page = self.client.get(reverse('review_queue'), {'page': 2, 'q': 'workspace-student'})
        self.assertEqual(page.context['page'].paginator.count, 28)
        self.assertEqual(len(page.context['page']), 3)
        self.submission.status = 'accepted'
        self.submission.reviewed_at = timezone.now()
        self.submission.save()
        page = self.client.get(reverse('review_queue'), {'mode': 'reviewed'})
        self.assertEqual(list(page.context['page']), [self.submission])

    def test_new_slide_has_outline_without_accessing_unsaved_relations(self):
        page = self.client.get(reverse('step_create', args=[self.lesson.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, reverse('step_editor', args=[self.lesson.pk, self.step.pk]))
        self.assertTrue(page.context['teacher_workspace'])
        self.assertEqual(page.context['workspace_section'], 'courses')

    def test_student_navigation_has_no_teacher_tools(self):
        self.client.force_login(self.student)
        page = self.client.get(reverse('dashboard'))
        self.assertFalse(page.context['teacher_workspace'])
        self.assertNotContains(page, reverse('media_library'))

    def test_home_queries_do_not_grow_with_result_rows(self):
        with CaptureQueriesContext(connection) as before:
            self.client.get(reverse('teacher'))
        now = timezone.now()
        ExamAttempt.objects.bulk_create([ExamAttempt(user=self.student, variant=self.variant, deadline=now, submitted_at=now) for _ in range(10)])
        with CaptureQueriesContext(connection) as after:
            self.client.get(reverse('teacher'))
        self.assertLessEqual(len(after), len(before))
