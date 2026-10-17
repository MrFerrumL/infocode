import io
import tempfile
from PIL import Image
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import Task, Topic, TaskBlock, Course, Section, Lesson, Step, Enrollment, StepProgress, StepSubmission, ExamAttempt
from .services import create_variant


class EditorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user('author', is_staff=True)
        cls.other = User.objects.create_user('another', is_staff=True)
        cls.student = User.objects.create_user('pupil')
        cls.topic = Topic.objects.create(number=1, title='Тема')
        cls.task = Task.objects.create(topic=cls.topic, title='Задача', statement='Условие', answer='42')
        cls.course = Course.objects.create(title='Курс', owner=cls.teacher, published=True)
        cls.section = Section.objects.create(course=cls.course, title='Модуль')
        cls.lesson = Lesson.objects.create(section=cls.section, title='Урок', published=True)
        cls.step = Step.objects.create(lesson=cls.lesson, title='Тест', kind='quiz', body='Условие', options=['A', 'B'], correct_choices=[1], quiz_mode='single')
        Enrollment.objects.create(user=cls.student, course=cls.course)

    def setUp(self):
        self.client.force_login(self.teacher)
        self.tmp = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.tmp.name)
        self.override.enable()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.override.disable)

    def image(self):
        stream = io.BytesIO()
        Image.new('RGB', (12, 12), 'white').save(stream, format='PNG')
        return SimpleUploadedFile('scheme.png', stream.getvalue(), content_type='image/png')

    def task_data(self):
        return {'title': 'Изображение без текста', 'topic': self.topic.pk, 'answer': '42', 'difficulty': 'ege', 'published': 'on'}

    def quiz_data(self):
        return {'title': self.step.title, 'kind': 'quiz', 'body': self.step.body, 'quiz_mode': 'single', 'points': 1, 'max_attempts': 0, 'published': 'on', 'options-TOTAL_FORMS': 3, 'options-INITIAL_FORMS': 2, 'options-0-text': 'A', 'options-1-text': 'B', 'options-1-correct': 'on', 'options-2-text': ''}

    def test_create_image_condition_and_mixed_solution_in_one_save(self):
        data = self.task_data()
        code = 'for x in range(3):\n    print(x)\n'
        data.update(statement_image=self.image(), solution='Объяснение', solution_image=self.image(), solution_file=SimpleUploadedFile('answer.py', b'print(42)'), solution_code=code)
        r = self.client.post('/teacher/tasks/new/', data)
        self.assertEqual(r.status_code, 302)
        task = Task.objects.get(title=data['title'])
        self.assertTrue(task.published)
        self.assertEqual(list(task.content_blocks.filter(purpose='statement').values_list('kind', flat=True)), ['image'])
        self.assertEqual(list(task.content_blocks.filter(purpose='solution').values_list('kind', flat=True)), ['text', 'image', 'file', 'code'])
        self.assertEqual(task.content_blocks.get(kind='code').text, code)
        self.assertEqual(task.solution, '')

    def test_quick_upload_validates_file_type_and_size(self):
        data = self.task_data()
        data.update(statement='Текст', solution_file=SimpleUploadedFile('answer.html', b'<script>bad</script>'))
        self.assertEqual(self.client.post('/teacher/tasks/new/', data).status_code, 200)
        self.assertFalse(Task.objects.filter(title=data['title']).exists())
        data['solution_file'] = SimpleUploadedFile('large.txt', b'x' * (50 * 1024 * 1024 + 1))
        self.assertEqual(self.client.post('/teacher/tasks/new/', data).status_code, 200)
        self.assertFalse(Task.objects.filter(title=data['title']).exists())

    def test_solution_only_cannot_publish_task(self):
        self.task.statement = ''
        self.task.save()
        TaskBlock.objects.create(task=self.task, purpose='solution', kind='text', text='Разбор')
        data = self.task_data()
        self.assertEqual(self.client.post(f'/teacher/tasks/{self.task.pk}/edit/', data).status_code, 200)

    def test_solution_code_and_files_hidden_until_attempt_and_escaped(self):
        code = TaskBlock.objects.create(task=self.task, purpose='solution', kind='code', text='print("<script>secret</script>")')
        file = TaskBlock.objects.create(task=self.task, purpose='solution', kind='file', file=SimpleUploadedFile('answer.py', b'print(42)'))
        self.client.force_login(self.student)
        self.assertNotContains(self.client.get(f'/tasks/{self.task.pk}/'), 'secret')
        self.assertEqual(self.client.get(f'/files/block/{file.pk}/').status_code, 404)
        self.assertNotContains(self.client.get('/catalog/?q=secret'), self.task.title)
        self.client.post(f'/tasks/{self.task.pk}/', {'answer': '0'})
        page = self.client.get(f'/tasks/{self.task.pk}/')
        self.assertContains(page, '&lt;script&gt;secret&lt;/script&gt;')
        self.assertNotContains(page, '<script>secret</script>')
        r = self.client.get(f'/files/block/{file.pk}/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('attachment;', r['Content-Disposition'])
        r.close()

    def test_solution_snapshot_frozen_and_locked_before_exam_submission(self):
        block = TaskBlock.objects.create(task=self.task, purpose='solution', kind='file', text='Секретный файл', file=SimpleUploadedFile('answer.py', b'print(42)'))
        v = create_variant(creator=self.teacher, title='Работа', topics=[], task_ids=str(self.task.pk), published=True)
        item = v.items.get()
        block.delete()
        self.assertNotContains(self.client.get(f'/teacher/variants/{v.pk}/print/'), 'Секретный файл')
        self.assertContains(self.client.get(f'/teacher/variants/{v.pk}/print/?answers=1'), 'Секретный файл')
        self.client.force_login(self.student)
        url = f'/files/snapshot/{item.pk}/?block=0'
        self.assertEqual(self.client.get(url).status_code, 404)
        attempt = ExamAttempt.objects.create(user=self.student, variant=v, deadline=timezone.now())
        self.assertEqual(self.client.get(url).status_code, 404)
        attempt.submitted_at = timezone.now()
        attempt.save()
        r = self.client.get(url)
        self.assertEqual(b''.join(r.streaming_content), b'print(42)')
        r.close()

    def test_block_order_is_independent_for_condition_and_solution(self):
        first = TaskBlock.objects.create(task=self.task, purpose='statement', kind='text', text='Первый', position=1)
        solution = TaskBlock.objects.create(task=self.task, purpose='solution', kind='text', text='Разбор', position=1)
        second = TaskBlock.objects.create(task=self.task, purpose='statement', kind='text', text='Второй', position=2)
        self.client.post(f'/teacher/blocks/{second.pk}/action/', {'action': 'up'})
        self.assertEqual(list(self.task.content_blocks.filter(purpose='statement').values_list('pk', flat=True)), [second.pk, first.pk])
        solution.refresh_from_db()
        self.assertEqual(solution.position, 1)

    def test_task_copy_is_draft_and_independent(self):
        original = TaskBlock.objects.create(task=self.task, purpose='solution', kind='code', text='print(42)')
        r = self.client.post(f'/teacher/tasks/{self.task.pk}/copy/')
        self.assertEqual(r.status_code, 302)
        copied = Task.objects.exclude(pk=self.task.pk).get()
        self.assertFalse(copied.published)
        block = copied.content_blocks.get()
        self.assertEqual(block.purpose, 'solution')
        block.text = 'print(100)'
        block.save()
        original.refresh_from_db()
        self.assertEqual(original.text, 'print(42)')

    def test_quiz_rows_save_and_delete_option_remaps_correct_answer(self):
        data = self.quiz_data()
        data.update({'options-0-DELETE': 'on', 'options-2-text': 'C'})
        r = self.client.post(f'/teacher/lessons/{self.lesson.pk}/steps/{self.step.pk}/', data)
        self.assertEqual(r.status_code, 302)
        self.step.refresh_from_db()
        self.assertEqual(self.step.options, ['B', 'C'])
        self.assertEqual(self.step.correct_choices, [0])
        self.assertEqual(self.step.revision, 2)

    def test_title_only_edit_preserves_revision(self):
        data = self.quiz_data()
        data['title'] = 'Новое название'
        r = self.client.post(f'/teacher/lessons/{self.lesson.pk}/steps/{self.step.pk}/', data)
        self.assertEqual(r.status_code, 302)
        self.step.refresh_from_db()
        self.assertEqual(self.step.revision, 1)

    def test_single_choice_rejects_two_correct_and_multiple_accepts(self):
        data = self.quiz_data()
        data['options-0-correct'] = 'on'
        url = f'/teacher/lessons/{self.lesson.pk}/steps/{self.step.pk}/'
        self.assertEqual(self.client.post(url, data).status_code, 200)
        data['quiz_mode'] = 'multiple'
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.step.refresh_from_db()
        self.assertEqual(self.step.correct_choices, [0, 1])
        self.client.force_login(self.student)
        self.assertContains(self.client.get(f'/learn/{self.step.pk}/'), 'type="checkbox"')
        self.client.post(f'/learn/{self.step.pk}/', {'choice': ['0', '1']})
        self.assertTrue(StepProgress.objects.get(user=self.student, step=self.step).completed)

    def test_draft_test_can_be_saved_but_not_published_incomplete(self):
        data = self.quiz_data()
        data.pop('published')
        data.pop('options-1-correct')
        url = f'/teacher/lessons/{self.lesson.pk}/steps/{self.step.pk}/'
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.client.post(f'/teacher/structure/step/{self.step.pk}/', {'action': 'toggle'})
        self.step.refresh_from_db()
        self.assertFalse(self.step.published)

    def test_add_option_preserves_text_without_saving(self):
        data = self.quiz_data()
        data.update(title='Ещё не сохранено', action='add_option')
        page = self.client.post(f'/teacher/lessons/{self.lesson.pk}/steps/{self.step.pk}/', data)
        self.assertContains(page, 'Ещё не сохранено')
        self.assertContains(page, 'name="options-3-text"')
        self.step.refresh_from_db()
        self.assertEqual(self.step.title, 'Тест')

    def test_lesson_template_and_course_copy_without_progress(self):
        r = self.client.post(f'/teacher/sections/{self.section.pk}/lessons/new/', {'title': 'С шаблоном', 'minutes': 20, 'template': 'standard'})
        self.assertEqual(r.status_code, 302)
        lesson = Lesson.objects.get(title='С шаблоном')
        self.assertEqual(list(lesson.steps.values_list('kind', flat=True)), ['video', 'quiz', 'text', 'file'])
        self.assertFalse(lesson.steps.filter(published=True).exists())
        self.client.post(f'/teacher/courses/{self.course.pk}/copy/')
        copy = Course.objects.exclude(pk=self.course.pk).get()
        self.assertFalse(copy.published)
        self.assertEqual(Step.objects.filter(lesson__section__course=copy).count(), 5)
        self.assertFalse(Enrollment.objects.filter(course=copy).exists())
        self.assertFalse(StepProgress.objects.filter(step__lesson__section__course=copy).exists())

    def test_copy_permissions(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(f'/teacher/courses/{self.course.pk}/copy/').status_code, 404)
        self.assertEqual(self.client.post(f'/teacher/structure/step/{self.step.pk}/', {'action': 'duplicate'}).status_code, 403)
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(f'/teacher/tasks/{self.task.pk}/copy/').status_code, 302)
        self.assertEqual(Task.objects.count(), 1)

    def test_type_specific_step_forms_and_presets(self):
        url = f'/teacher/lessons/{self.lesson.pk}/steps/new/'
        for kind in ['text', 'video', 'quiz', 'answer', 'file']:
            page = self.client.get(url + '?kind=' + kind)
            self.assertEqual(page.status_code, 200)
            if kind != 'video':
                self.assertNotContains(page, 'name="video"')
            # Every editor can switch to a quiz without discarding its material.
            self.assertContains(page, 'options-TOTAL_FORMS')
            self.assertContains(page, 'data-slide-editor')
            self.assertEqual(page.context['form']['kind'].value(), kind)
        self.assertContains(self.client.get(url + '?kind=quiz&preset=boolean'), 'Неверно')

    def test_code_only_lesson_and_save_preview(self):
        r = self.client.post(f'/teacher/lessons/{self.lesson.pk}/steps/new/', {'title': 'Пример', 'kind': 'text', 'code': '    print("<b>test</b>")\n', 'points': 0, 'max_attempts': 0, 'published': 'on', 'action': 'preview'})
        self.assertEqual(r.status_code, 302)
        step = Step.objects.get(title='Пример')
        self.assertEqual(r.url, f'/learn/{step.pk}/')
        self.assertTrue(step.code.startswith('    '))
        self.assertContains(self.client.get(r.url), '&lt;b&gt;test&lt;/b&gt;')

    def test_editor_filters(self):
        Task.objects.create(topic=self.topic, title='Черновик Гроб', answer='1', difficulty='grave', published=False)
        page = self.client.get('/teacher/tasks/?difficulty=grave&status=draft')
        self.assertContains(page, 'Черновик Гроб')
        self.assertNotContains(page, f'/teacher/tasks/{self.task.pk}/edit/')

    def test_text_title_edit_does_not_reset_progress(self):
        step = Step.objects.create(lesson=self.lesson, kind='text', title='Текст', body='Теория', points=0)
        StepProgress.objects.create(user=self.student, step=step, completed=True)
        r = self.client.post(f'/teacher/lessons/{self.lesson.pk}/steps/{step.pk}/', {'title': 'Новое название', 'kind': 'text', 'body': 'Теория', 'points': 0, 'max_attempts': 0, 'published': 'on'})
        self.assertEqual(r.status_code, 302)
        step.refresh_from_db()
        self.assertEqual(step.revision, 1)
        self.assertTrue(StepProgress.objects.get(user=self.student, step=step, revision=step.revision).completed)
