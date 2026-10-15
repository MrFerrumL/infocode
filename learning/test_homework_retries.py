from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .assignments import WorkForm
from .models import (AssignmentRecipient, ExamAnswer, ExamAttempt, HomeworkSubmission,
                     LearningAssignment, Notification, StudentAssignment, Task, Topic)
from .security import form_token
from .services import create_variant


class HomeworkRetryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user('homework-teacher', is_staff=True)
        cls.other_teacher = User.objects.create_user('homework-other-teacher', is_staff=True)
        cls.student = User.objects.create_user('homework-student')
        cls.other_student = User.objects.create_user('homework-other-student')
        StudentAssignment.objects.create(teacher=cls.teacher, student=cls.student)
        # Sharing a pupil must not grant access to another teacher's private work.
        StudentAssignment.objects.create(teacher=cls.other_teacher, student=cls.student)
        topic = Topic.objects.create(number=6, title='Алгоритмы')
        cls.task = Task.objects.create(topic=topic, title='Первая', statement='Условие первой', answer='42')
        cls.second_task = Task.objects.create(topic=topic, title='Вторая', statement='Условие второй', answer='7')

    def setUp(self):
        self.client.force_login(self.student)

    def work(self, target='variant', **changes):
        now = timezone.now()
        data = dict(teacher=self.teacher, title='Повторяемая домашка', kind='homework', target=target,
                    starts_at=now-timedelta(hours=1), due_at=now+timedelta(hours=2),
                    max_attempts=0, pass_percent=100)
        if target == 'variant':
            variant = create_variant(creator=self.teacher, title=data['title'], topics=[],
                task_ids=f'{self.task.pk},{self.second_task.pk}', duration=60, published=True)
            variant.assigned_only = True
            variant.save(update_fields=['assigned_only'])
            data['variant'] = variant
        data.update(changes)
        work = LearningAssignment.objects.create(**data)
        AssignmentRecipient.objects.create(assignment=work, student=self.student)
        return work

    def detail_url(self, work):
        return reverse('assignment_detail', args=[work.pk])

    def start(self, work, token=None):
        data = {'submit_token': token or self.client.get(self.detail_url(work)).context['start_token']}
        response = self.client.post(reverse('start_exam', args=[work.variant_id]), data)
        self.assertEqual(response.status_code, 302)
        return ExamAttempt.objects.get(variant=work.variant, user=self.student, submitted_at=None)

    def send(self, attempt, correct=True, **changes):
        data = {'action': 'submit'}
        for item in attempt.variant.items.all():
            data[f'answer_{item.pk}'] = item.answer if correct else '0'
            data[f'code_{item.pk}-python_code'] = f'for i in range({item.position}):\n    print(i)'
            data[f'code_{item.pk}-comment'] = f'Пояснение к задаче {item.position}'
        data.update(changes)
        return self.client.post(reverse('exam', args=[attempt.pk]), data)

    def test_passed_homework_can_be_repeated_with_independent_empty_attempts(self):
        work = self.work()
        first = self.start(work)
        self.assertEqual(self.send(first).status_code, 302)
        original = list(first.answers.values('item_id', 'answer', 'python_code', 'comment', 'correct'))
        completed = work.recipients.get().completed_at
        self.assertIsNotNone(completed)
        self.assertContains(self.client.get(self.detail_url(work)), 'Начать заново')
        second = self.start(work)
        self.assertNotEqual(first.pk, second.pk)
        self.assertFalse(second.answers.exists())
        fresh = self.client.get(reverse('exam', args=[second.pk]))
        self.assertNotContains(fresh, 'Пояснение к задаче 1')
        for item in fresh.context['items']:
            self.assertEqual(item.value, '')
            self.assertEqual(item.code_form['python_code'].value(), '')
            self.assertEqual(item.code_form['comment'].value(), '')
        self.assertEqual(self.send(second, correct=False).status_code, 302)
        self.assertEqual(work.recipients.get().completed_at, completed)
        self.assertEqual(list(first.answers.values('item_id', 'answer', 'python_code', 'comment', 'correct')), original)
        page = self.client.get(self.detail_url(work))
        self.assertEqual(page.context['attempt_summary'], {'count': 2, 'finished': 2, 'best': 2, 'latest': 0})
        self.assertEqual([a.number for a in page.context['attempts']], [2, 1])
        self.start(work)
        self.assertEqual(ExamAttempt.objects.filter(variant=work.variant).count(), 3)

    def test_draft_saves_code_and_comment_for_each_task_and_restores_them(self):
        work = self.work()
        attempt = self.start(work)
        self.assertEqual(self.send(attempt, action='save').status_code, 302)
        self.assertIsNone(ExamAttempt.objects.get(pk=attempt.pk).submitted_at)
        page = self.client.get(reverse('exam', args=[attempt.pk]))
        for item in page.context['items']:
            answer = attempt.answers.get(item=item)
            self.assertIn('    print(i)', answer.python_code)
            self.assertEqual(answer.comment, f'Пояснение к задаче {item.position}')
            self.assertEqual(item.code_form['comment'].value(), answer.comment)
        # The bank and the issued snapshot keep their original policy.
        self.assertEqual(work.variant.items.first().code_mode, 'off')
        self.assertEqual(Task.objects.get(pk=self.task.pk).code_mode, 'off')

    def test_teacher_sees_every_result_code_comment_and_summary(self):
        work = self.work()
        attempts = []
        for correct in [True, False]:
            attempt = self.start(work)
            self.send(attempt, correct=correct)
            attempts.append(attempt)
        self.client.force_login(self.teacher)
        page = self.client.get(self.detail_url(work))
        recipient = page.context['recipients'][0]
        self.assertEqual(recipient.attempt_summary['best'], 2)
        self.assertEqual(recipient.attempt_summary['latest'], 0)
        self.assertContains(page, 'Все попытки (2)')
        for attempt in attempts:
            result_url = reverse('result', args=[attempt.pk])
            self.assertContains(page, result_url)
            result = self.client.get(result_url)
            self.assertContains(result, 'Пояснение к задаче 1')
            self.assertContains(result, 'Пояснение к задаче 2')
            self.assertContains(result, '    print(i)')
        self.assertEqual(Notification.objects.filter(user=self.teacher, title__startswith='Сдана работа:').count(), 2)

    def test_repeated_start_and_stale_token_do_not_create_extra_attempts(self):
        work = self.work()
        token = self.client.get(self.detail_url(work)).context['start_token']
        first = self.start(work, token)
        self.assertEqual(self.start(work, token).pk, first.pk)
        self.send(first)
        self.client.post(reverse('start_exam', args=[work.variant_id]), {'submit_token': token})
        self.assertEqual(ExamAttempt.objects.filter(variant=work.variant).count(), 1)

    def test_retry_limit_and_extra_attempt_work_even_after_success(self):
        work = self.work(max_attempts=2)
        self.send(self.start(work))
        self.send(self.start(work), correct=False)
        self.assertContains(self.client.get(self.detail_url(work)), 'Попытки закончились')
        self.client.post(reverse('start_exam', args=[work.variant_id]))
        self.assertEqual(ExamAttempt.objects.filter(variant=work.variant).count(), 2)
        self.client.force_login(self.teacher)
        self.client.post(self.detail_url(work), {'action': 'extra', 'recipient': work.recipients.get().pk})
        self.client.force_login(self.student)
        self.start(work)
        self.assertEqual(ExamAttempt.objects.filter(variant=work.variant).count(), 3)

    def test_deadlines_and_closed_assignments_still_block_retries(self):
        work = self.work()
        self.send(self.start(work))
        work.due_at = timezone.now()-timedelta(seconds=1)
        work.save()
        self.assertContains(self.client.get(self.detail_url(work)), 'Срок сдачи истёк')
        self.client.post(reverse('start_exam', args=[work.variant_id]))
        self.assertEqual(ExamAttempt.objects.filter(variant=work.variant).count(), 1)
        work.allow_late = True
        work.save()
        self.send(self.start(work), correct=False)
        work.active = False
        work.save()
        self.client.post(reverse('start_exam', args=[work.variant_id]))
        self.assertEqual(ExamAttempt.objects.filter(variant=work.variant).count(), 2)

    def test_expired_attempt_is_archived_before_starting_again(self):
        work = self.work()
        first = self.start(work)
        self.send(first, action='save')
        ExamAttempt.objects.filter(pk=first.pk).update(deadline=timezone.now()-timedelta(seconds=1))
        second = self.start(work)
        first.refresh_from_db()
        self.assertIsNotNone(first.submitted_at)
        self.assertEqual(first.score, 2)
        self.assertNotEqual(first.pk, second.pk)
        self.assertFalse(second.answers.exists())

    def test_validation_is_atomic_and_keeps_previous_answers(self):
        work = self.work()
        attempt = self.start(work)
        self.send(attempt, action='save')
        before = list(attempt.answers.values())
        item = work.variant.items.first()
        for field, value in [('comment', 'x'*3001), ('python_code', 'x'*20001), ('comment', 'bad\x00text')]:
            response = self.send(attempt, correct=False, **{f'code_{item.pk}-{field}': value})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(list(attempt.answers.values()), before)
            self.assertIsNone(ExamAttempt.objects.get(pk=attempt.pk).submitted_at)

    def test_completed_attempt_cannot_be_changed_and_user_text_is_escaped(self):
        work = self.work()
        attempt = self.start(work)
        item = work.variant.items.first()
        source = '<script>alert("code")</script>'
        self.send(attempt, **{f'code_{item.pk}-comment': source, f'code_{item.pk}-python_code': source})
        before = list(attempt.answers.values())
        self.send(attempt, correct=False)
        self.assertEqual(list(attempt.answers.values()), before)
        page = self.client.get(reverse('result', args=[attempt.pk]))
        self.assertNotContains(page, source)
        self.assertContains(page, '&lt;script&gt;')

    def test_private_attempts_are_only_visible_to_pupil_and_assignment_owner(self):
        work = self.work()
        attempt = self.start(work)
        self.send(attempt)
        for user in [self.other_student, self.other_teacher]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.detail_url(work)).status_code, 403)
            self.assertEqual(self.client.get(reverse('result', args=[attempt.pk])).status_code, 404)
            self.assertEqual(self.client.post(reverse('exam', args=[attempt.pk]), {'action': 'save'}).status_code, 404)
        # The author retains historical submissions if the pupil is detached later.
        StudentAssignment.objects.filter(teacher=self.teacher, student=self.student).delete()
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(reverse('result', args=[attempt.pk])).status_code, 200)

    def test_teacher_can_change_limit_without_losing_attempts_or_acceptance(self):
        work = self.work()
        self.send(self.start(work))
        completed = work.recipients.get().completed_at
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.post(self.detail_url(work), {'action': 'limit', 'max_attempts': 3}).status_code, 302)
        self.assertEqual(LearningAssignment.objects.get(pk=work.pk).max_attempts, 3)
        self.assertEqual(work.recipients.get().completed_at, completed)
        self.assertEqual(ExamAttempt.objects.filter(variant=work.variant).count(), 1)
        self.assertEqual(self.client.post(self.detail_url(work), {'action': 'limit', 'max_attempts': -1}).status_code, 200)
        self.assertEqual(LearningAssignment.objects.get(pk=work.pk).max_attempts, 3)
        self.client.force_login(self.student)
        self.client.post(self.detail_url(work), {'action': 'limit', 'max_attempts': 100})
        self.assertEqual(LearningAssignment.objects.get(pk=work.pk).max_attempts, 3)

    def test_manual_homework_allows_independent_pending_and_reviewed_attempts_with_code(self):
        work = self.work(target='manual')
        data = {'text': 'Объяснение', 'python_code': 'for i in range(3):\n    print(i)',
                'submit_token': form_token(self.student, f'homework:{work.pk}')}
        self.client.post(self.detail_url(work), data)
        self.client.post(self.detail_url(work), data)
        first = HomeworkSubmission.objects.get()
        data['submit_token'] = form_token(self.student, f'homework:{work.pk}')
        data['python_code'] = 'print(7)'
        self.client.post(self.detail_url(work), data)
        self.assertEqual(HomeworkSubmission.objects.count(), 2)
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(reverse('review_homework', args=[first.pk])), '    print(i)')
        self.client.post(reverse('review_homework', args=[first.pk]), {'status': 'accepted', 'score': 100})
        completed = work.recipients.get().completed_at
        self.client.force_login(self.student)
        self.client.post(self.detail_url(work), {'python_code': 'print(42)'})
        self.assertEqual(HomeworkSubmission.objects.count(), 3)
        self.assertEqual(work.recipients.get().completed_at, completed)
        first.refresh_from_db()
        self.assertEqual(first.python_code, 'for i in range(3):\n    print(i)')
        self.assertEqual(first.status, 'accepted')

    def test_manual_empty_or_oversized_code_is_rejected(self):
        work = self.work(target='manual')
        for code in ['   \n', 'x'*20001, 'bad\x00text']:
            self.assertEqual(self.client.post(self.detail_url(work), {'python_code': code}).status_code, 200)
        self.assertFalse(HomeworkSubmission.objects.exists())

    def test_required_kr_keeps_limit_and_stops_after_passing(self):
        work = self.work(kind='required_test', max_attempts=3)
        self.send(self.start(work))
        self.client.post(reverse('start_exam', args=[work.variant_id]))
        self.assertEqual(ExamAttempt.objects.filter(variant=work.variant).count(), 1)

    def test_default_work_form_and_kr_validation(self):
        self.assertEqual(WorkForm(user=self.teacher).initial['max_attempts'], 0)
        self.assertEqual(WorkForm(user=self.teacher, initial={'kind': 'required_test'}).initial['max_attempts'], 1)
        work = self.work(kind='required_test', max_attempts=0)
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            work.full_clean()

    def test_migration_enables_previous_default_without_losing_history_or_custom_limits(self):
        ordinary = self.work(max_attempts=1)
        manual = self.work(target='manual', max_attempts=1)
        custom = self.work(max_attempts=4)
        kr = self.work(kind='required_test', max_attempts=1)
        self.send(self.start(ordinary))
        completed = ordinary.recipients.get().completed_at
        migration = import_module('learning.migrations.0013_homework_retries_and_comments')
        migration.enable_homework_retries(apps, SimpleNamespace(connection=connection))
        self.assertEqual(list(LearningAssignment.objects.filter(pk__in=[ordinary.pk, manual.pk, custom.pk, kr.pk]).order_by('pk').values_list('max_attempts', flat=True)), [0, 0, 4, 1])
        self.assertEqual(ordinary.recipients.get().completed_at, completed)
        self.assertEqual(ExamAnswer.objects.count(), 2)

    def test_start_and_answers_require_csrf_in_browser(self):
        work = self.work()
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.student)
        self.assertEqual(browser.post(reverse('start_exam', args=[work.variant_id])).status_code, 403)
        attempt = self.start(work)
        self.assertEqual(browser.post(reverse('exam', args=[attempt.pk]), {'action': 'submit'}).status_code, 403)
