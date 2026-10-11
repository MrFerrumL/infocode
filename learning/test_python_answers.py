from datetime import timedelta
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from .models import (Task, Topic, Practice, Course, Section, Lesson, Step, Enrollment,
                     StepSubmission, StepProgress, StudentAssignment, ExamAttempt, ExamAnswer,
                     LearningAssignment, ActionReceipt)
from .services import create_variant, finish_attempt


SOURCE = 'for value in range(3):\n    if value > 0:\n        print(value)\n'


class PythonAnswerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user('python-teacher', is_staff=True)
        cls.other_teacher = User.objects.create_user('python-other-teacher', is_staff=True)
        cls.student = User.objects.create_user('python-student')
        cls.other_student = User.objects.create_user('python-other-student')
        StudentAssignment.objects.create(teacher=cls.teacher, student=cls.student)
        cls.topic = Topic.objects.create(number=6, title='Алгоритмы')
        cls.task = Task.objects.create(topic=cls.topic, title='Задача с программой', statement='Найдите ответ', answer='42')
        cls.course = Course.objects.create(title='Курс', owner=cls.teacher, published=True)
        cls.section = Section.objects.create(course=cls.course, title='Раздел')
        cls.lesson = Lesson.objects.create(section=cls.section, title='Урок', published=True)
        cls.step = Step.objects.create(lesson=cls.lesson, title='Решение', kind='answer', body='Посчитайте', answer='42')
        Enrollment.objects.create(user=cls.student, course=cls.course)

    def setUp(self):
        self.client.force_login(self.student)

    def task_mode(self, mode):
        self.task.code_mode = mode
        self.task.save()

    def step_mode(self, mode):
        self.step.code_mode = mode
        self.step.save()

    def task_url(self):
        return f'/tasks/{self.task.pk}/'

    def step_url(self):
        return f'/learn/{self.step.pk}/'

    def variant(self):
        return create_variant(creator=self.teacher, title='Контрольная Python', topics=[], task_ids=str(self.task.pk), published=True)

    def attempt(self, variant):
        self.client.post(f'/variants/{variant.pk}/start/')
        return ExamAttempt.objects.get(variant=variant, user=self.student, submitted_at=None)

    def test_disabled_task_has_no_field_and_ignores_injected_code(self):
        self.assertNotContains(self.client.get(self.task_url()), 'name="python_code"')
        self.client.post(self.task_url(), {'answer': '42', 'python_code': SOURCE})
        result = Practice.objects.get()
        self.assertTrue(result.correct)
        self.assertEqual(result.python_code, '')

    def test_optional_code_keeps_indentation_and_regular_grading(self):
        self.task_mode('optional')
        self.assertContains(self.client.get(self.task_url()), 'name="python_code"')
        self.client.post(self.task_url(), {'answer': '0', 'python_code': SOURCE})
        result = Practice.objects.get()
        self.assertFalse(result.correct)
        self.assertEqual(result.python_code, SOURCE)
        self.client.post(self.task_url(), {'answer': '42'})
        self.assertTrue(Practice.objects.latest('pk').correct)
        self.assertEqual(Practice.objects.latest('pk').python_code, '')

    def test_required_code_rejects_empty_without_spending_submission_token(self):
        self.task_mode('required')
        token = self.client.get(self.task_url()).context['submit_token']
        data = {'answer': '42', 'python_code': '  \n\t', 'submit_token': token}
        response = self.client.post(self.task_url(), data)
        self.assertContains(response, 'Приложите Python-код')
        self.assertFalse(Practice.objects.exists())
        self.assertFalse(ActionReceipt.objects.exists())
        data['python_code'] = SOURCE
        self.client.post(self.task_url(), data)
        self.client.post(self.task_url(), data)
        self.assertEqual(Practice.objects.count(), 1)
        self.assertEqual(Practice.objects.get().python_code, SOURCE)

    def test_oversized_and_null_code_are_rejected_not_truncated(self):
        self.task_mode('optional')
        for source in ['x' * 20001, 'print(1)\x00']:
            with self.subTest(length=len(source)):
                response = self.client.post(self.task_url(), {'answer': '42', 'python_code': source})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
                self.assertFalse(Practice.objects.exists())

    def test_code_is_text_and_history_is_private(self):
        self.task_mode('optional')
        source = 'raise RuntimeError("do not execute")\nprint("</textarea><script>alert(12345)</script>")'
        response = self.client.post(self.task_url(), {'answer': '42', 'python_code': source})
        self.assertNotContains(response, '<script>alert(12345)</script>')
        self.assertContains(response, '&lt;script&gt;alert(12345)&lt;/script&gt;')
        self.assertEqual(Practice.objects.get().python_code, source)
        self.client.force_login(self.other_student)
        self.assertNotContains(self.client.get(self.task_url()), 'do not execute')
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(f'/teacher/students/{self.student.pk}/'), 'do not execute')
        self.client.force_login(self.other_teacher)
        self.assertEqual(self.client.get(f'/teacher/students/{self.student.pk}/').status_code, 404)

    def test_teacher_can_enable_code_on_task(self):
        self.client.force_login(self.teacher)
        url = f'/teacher/tasks/{self.task.pk}/edit/'
        self.assertContains(self.client.get(url), 'Python-код в ответе ученика')
        response = self.client.post(url, {'title': self.task.title, 'topic': self.topic.pk,
                                         'difficulty': 'easy', 'answer': '42', 'statement': 'Условие',
                                         'code_mode': 'required', 'published': 'on'})
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db()
        self.assertEqual(self.task.code_mode, 'required')

    def test_variant_code_policy_is_frozen_with_tasks(self):
        self.task_mode('required')
        variant = self.variant()
        self.task_mode('off')
        self.assertEqual(variant.items.get().code_mode, 'required')
        attempt = self.attempt(variant)
        self.assertContains(self.client.get(f'/exam/{attempt.pk}/'), 'Решение на Python')

    def test_exam_saves_code_and_shows_it_on_result(self):
        self.task_mode('optional')
        variant = self.variant()
        attempt = self.attempt(variant)
        item = variant.items.get()
        data = {f'answer_{item.pk}': '42', f'code_{item.pk}-python_code': SOURCE, 'action': 'save'}
        self.assertEqual(self.client.post(f'/exam/{attempt.pk}/', data).status_code, 302)
        self.assertEqual(ExamAnswer.objects.get().python_code, SOURCE)
        self.assertContains(self.client.get(f'/exam/{attempt.pk}/'), 'for value in range(3):')
        data['action'] = 'submit'
        self.client.post(f'/exam/{attempt.pk}/', data)
        attempt.refresh_from_db()
        self.assertEqual(attempt.score, 1)
        self.assertContains(self.client.get(f'/results/{attempt.pk}/'), 'for value in range(3):')
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(f'/results/{attempt.pk}/'), 'for value in range(3):')
        self.client.force_login(self.other_teacher)
        self.assertEqual(self.client.get(f'/results/{attempt.pk}/').status_code, 404)

    def test_required_exam_code_can_be_saved_as_draft_but_cannot_submit_answer_without_it(self):
        self.task_mode('required')
        variant = self.variant()
        attempt = self.attempt(variant)
        item = variant.items.get()
        data = {f'answer_{item.pk}': '42', 'action': 'save'}
        self.assertEqual(self.client.post(f'/exam/{attempt.pk}/', data).status_code, 302)
        data['action'] = 'submit'
        response = self.client.post(f'/exam/{attempt.pk}/', data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="42"')
        self.assertNotIn(' required', response.context['items'][0].code_form['python_code'].as_widget())
        attempt.refresh_from_db()
        self.assertIsNone(attempt.submitted_at)
        data[f'code_{item.pk}-python_code'] = SOURCE
        self.client.post(f'/exam/{attempt.pk}/', data)
        attempt.refresh_from_db()
        self.assertEqual(attempt.score, 1)

    def test_unanswered_exam_item_can_stay_blank_and_expiry_cannot_bypass_code_requirement(self):
        self.task_mode('required')
        variant = self.variant()
        attempt = self.attempt(variant)
        self.assertEqual(self.client.post(f'/exam/{attempt.pk}/', {'action': 'submit'}).status_code, 302)
        attempt.refresh_from_db()
        self.assertEqual(attempt.score, 0)
        attempt = self.attempt(variant)
        ExamAnswer.objects.create(attempt=attempt, item=variant.items.get(), answer='42')
        finish_attempt(attempt)
        self.assertEqual(attempt.score, 0)

    def test_oversized_exam_code_keeps_previous_saved_answers(self):
        self.task_mode('optional')
        variant = self.variant()
        attempt = self.attempt(variant)
        item = variant.items.get()
        ExamAnswer.objects.create(attempt=attempt, item=item, answer='previous', python_code=SOURCE)
        response = self.client.post(f'/exam/{attempt.pk}/', {'action': 'submit', f'answer_{item.pk}': '42', f'code_{item.pk}-python_code': 'x' * 20001})
        self.assertEqual(response.status_code, 200)
        saved = ExamAnswer.objects.get()
        self.assertEqual(saved.answer, 'previous')
        self.assertEqual(saved.python_code, SOURCE)

    def test_required_course_code_does_not_spend_attempt_on_validation_error(self):
        self.step_mode('required')
        token = self.client.get(self.step_url()).context['submit_token']
        data = {'action': 'answer', 'answer': '42', 'submit_token': token, 'python_code': '  '}
        response = self.client.post(self.step_url(), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="42"')
        self.assertFalse(StepSubmission.objects.exists())
        self.assertFalse(StepProgress.objects.exists())
        self.assertFalse(ActionReceipt.objects.exists())
        data['python_code'] = SOURCE
        self.client.post(self.step_url(), data)
        self.assertEqual(StepSubmission.objects.get().python_code, SOURCE)
        self.assertEqual(StepProgress.objects.get().attempts, 1)
        self.client.post(self.step_url(), data)
        self.assertEqual(StepSubmission.objects.count(), 1)

    def test_optional_course_code_history_and_teacher_preview(self):
        self.step_mode('optional')
        self.client.post(self.step_url(), {'answer': '42', 'python_code': SOURCE})
        self.assertContains(self.client.get(self.step_url()), 'for value in range(3):')
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(f'/teacher/students/{self.student.pk}/'), 'for value in range(3):')
        self.client.post(self.step_url(), {'answer': '42', 'python_code': 'print(999)'})
        self.assertEqual(StepSubmission.objects.count(), 1)

    def test_code_validation_preserves_quiz_and_pair_selections(self):
        self.step_mode('required')
        self.step.kind = 'quiz'
        self.step.options = ['A', 'B']
        self.step.correct_choices = [1]
        self.step.save()
        response = self.client.post(self.step_url(), {'choice': ['1'], 'python_code': ''})
        self.assertEqual(response.context['selected_choices'], [1])
        self.step.kind = 'matching'
        self.step.pairs = [{'prompt': 'Вопрос', 'answer': 'Ответ'}]
        self.step.save()
        response = self.client.post(self.step_url(), {'pair_0': 'Ответ', 'python_code': ''})
        self.assertEqual(response.context['pair_rows'], [{'prompt': 'Вопрос', 'value': 'Ответ'}])
        self.assertFalse(StepSubmission.objects.exists())

    def test_teacher_enables_course_code_with_new_revision(self):
        self.client.force_login(self.teacher)
        url = f'/teacher/lessons/{self.lesson.pk}/steps/{self.step.pk}/'
        self.assertContains(self.client.get(url), 'Python-код в ответе ученика')
        response = self.client.post(url, {'title': self.step.title, 'kind': 'answer', 'answer': '42',
                                         'points': self.step.points, 'max_attempts': 0, 'code_mode': 'required',
                                         'published': 'on', 'edit_version': self.step.edit_version,
                                         'blocks-TOTAL_FORMS': 1, 'blocks-INITIAL_FORMS': 0,
                                         'blocks-0-kind': 'text', 'blocks-0-position': 1, 'blocks-0-text': '<p>Посчитайте</p>'})
        self.assertEqual(response.status_code, 302)
        self.step.refresh_from_db()
        self.assertEqual(self.step.code_mode, 'required')
        self.assertEqual(self.step.revision, 2)

    def test_mandatory_kr_copies_code_policy_from_existing_variant_snapshot(self):
        self.task_mode('required')
        variant = self.variant()
        self.task_mode('off')
        self.client.force_login(self.teacher)
        now = timezone.localtime()
        response = self.client.post('/teacher/assignments/new/', {
            'title': 'КР с кодом', 'kind': 'required_test', 'target': 'variant', 'source': variant.pk,
            'students': [self.student.pk], 'count': 1, 'duration': 60,
            'starts_at': (now-timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
            'due_at': (now+timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M'),
            'max_attempts': 1, 'pass_percent': 100,
        })
        self.assertEqual(response.status_code, 302)
        work = LearningAssignment.objects.get()
        self.assertEqual(work.variant.items.get().code_mode, 'required')

    def create_code_work(self, policy, source=None):
        self.client.force_login(self.teacher)
        now = timezone.localtime()
        data = {'title': 'КР с отправкой кода', 'kind': 'required_test', 'target': 'variant',
                'students': [self.student.pk], 'duration': 60, 'count': 1, 'code_policy': policy,
                'starts_at': (now-timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
                'due_at': (now+timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M'),
                'max_attempts': 1, 'pass_percent': 100}
        data.update({'source': source.pk} if source else {'task_ids': str(self.task.pk)})
        return self.client.post('/teacher/assignments/new/', data)

    def test_teacher_can_enable_code_for_whole_kr_without_changing_bank_or_source(self):
        source = self.variant()
        response = self.create_code_work('optional', source)
        self.assertEqual(response.status_code, 302)
        work = LearningAssignment.objects.get()
        self.assertEqual(work.variant.items.get().code_mode, 'optional')
        self.assertEqual(source.items.get().code_mode, 'off')
        self.task.refresh_from_db()
        self.assertEqual(self.task.code_mode, 'off')

    def test_kr_student_code_reaches_teacher_via_results_and_notification(self):
        from .models import Notification
        from django.urls import reverse
        self.assertEqual(self.create_code_work('optional').status_code, 302)
        work = LearningAssignment.objects.get()
        self.client.force_login(self.student)
        attempt = self.attempt(work.variant)
        item = work.variant.items.get()
        self.assertContains(self.client.get(f'/exam/{attempt.pk}/'), 'Решение на Python')
        self.client.post(f'/exam/{attempt.pk}/', {
            'action': 'submit', f'answer_{item.pk}': '42', f'code_{item.pk}-python_code': SOURCE})
        detail_url = reverse('assignment_detail', args=[work.pk])
        self.assertTrue(Notification.objects.filter(user=self.teacher, url=detail_url).exists())
        self.client.force_login(self.teacher)
        page = self.client.get(detail_url)
        self.assertContains(page, f'/results/{attempt.pk}/')
        self.assertContains(page, 'Python-код: 1')
        self.assertContains(self.client.get(f'/results/{attempt.pk}/'), 'for value in range(3):')
        self.client.force_login(self.other_teacher)
        self.assertEqual(self.client.get(detail_url).status_code, 403)
        self.assertEqual(self.client.get(f'/results/{attempt.pk}/').status_code, 404)

    def test_required_and_disabled_work_overrides_and_invalid_policy(self):
        from .models import Variant
        from django.core.exceptions import ValidationError
        for mode in ('required', 'off'):
            self.assertEqual(self.create_code_work(mode).status_code, 302)
            self.assertEqual(LearningAssignment.objects.latest('pk').variant.items.get().code_mode, mode)
        before = Variant.objects.count()
        response = self.create_code_work('run-code-on-server')
        self.assertEqual(response.status_code, 200)
        self.assertIn('code_policy', response.context['form'].errors)
        self.assertEqual(Variant.objects.count(), before)
        with self.assertRaises(ValidationError):
            create_variant(creator=self.teacher, title='Некорректная', topics=[], task_ids=str(self.task.pk), code_policy='bad')
        self.assertEqual(Variant.objects.count(), before)

    def test_regular_variant_generator_can_allow_optional_code(self):
        from .forms import VariantForm
        form = VariantForm({'title': 'Обычная КР', 'kind': 'test', 'count': 1, 'duration': 60,
                            'task_ids': str(self.task.pk), 'code_policy': 'optional'})
        self.assertTrue(form.is_valid(), form.errors)
        variant = create_variant(creator=self.teacher, **form.cleaned_data)
        self.assertEqual(variant.items.get().code_mode, 'optional')
