import io
import json
import tempfile

from PIL import Image
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Course, Section, Lesson, Step, StepContentBlock, Enrollment, StepProgress, StepSubmission


class FreeLessonComposerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user('free-author', is_staff=True)
        cls.other = User.objects.create_user('free-other', is_staff=True)
        cls.student = User.objects.create_user('free-student')
        cls.course = Course.objects.create(title='Свободный курс', owner=cls.teacher, published=True)
        cls.section = Section.objects.create(course=cls.course, title='Раздел')
        cls.lesson = Lesson.objects.create(section=cls.section, title='Урок', published=True)
        Enrollment.objects.create(course=cls.course, user=cls.student)

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        settings = override_settings(MEDIA_ROOT=folder.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.client.force_login(self.teacher)

    def lesson_url(self):
        return reverse('lesson_create', args=[self.section.pk])

    def editor_url(self, step=None):
        return reverse('step_editor', args=[self.lesson.pk, step.pk]) if step else reverse('step_create', args=[self.lesson.pk])

    def content_data(self, rows, step=None, **extra):
        data = {'title': 'Слайд', 'kind': 'text', 'published': 'on', 'points': 1, 'max_attempts': 0,
                'blocks-TOTAL_FORMS': len(rows), 'blocks-INITIAL_FORMS': step.content_blocks.count() if step else 0}
        if step:
            data['edit_version'] = step.edit_version
        for i, row in enumerate(rows):
            data[f'blocks-{i}-position'] = i + 1
            for key, value in row.items():
                data[f'blocks-{i}-{key}'] = value
        data.update(extra)
        return data

    def save_slide(self, rows, step=None, **extra):
        response = self.client.post(self.editor_url(step), self.content_data(rows, step, **extra))
        self.assertEqual(response.status_code, 302,
                         str(response.context['form'].errors) + str(response.context['blocks'].errors) if response.status_code == 200 else '')
        return Step.objects.get(pk=step.pk) if step else self.lesson.steps.latest('pk')

    def test_new_lesson_has_free_choice_without_template_dropdown_or_database_writes(self):
        count = Lesson.objects.count()
        page = self.client.get(self.lesson_url())
        self.assertContains(page, 'data-lesson-composer')
        for key in ['text', 'quiz', 'answer', 'mixed', 'video']:
            self.assertContains(page, f'data-add-slide="{key}"')
        self.assertNotContains(page, 'Начать с шаблона')
        self.assertNotContains(page, 'name="template"')
        self.assertEqual(Lesson.objects.count(), count)

    def test_create_repeated_slides_in_exact_custom_order_with_names(self):
        types = ['quiz', 'answer', 'answer', 'video', 'text', 'mixed', 'boolean', 'multiple']
        plan = [{'type': kind, 'title': f'Мой слайд {i}'} for i, kind in enumerate(types)]
        response = self.client.post(self.lesson_url(), {'title': 'Мой порядок', 'minutes': 30,
            'slide_plan': json.dumps(plan), 'template': 'standard'}, follow=True)
        self.assertEqual(response.status_code, 200)
        lesson = Lesson.objects.get(title='Мой порядок')
        steps = list(lesson.steps.all())
        self.assertEqual([s.kind for s in steps], ['quiz', 'answer', 'answer', 'video', 'text', 'answer', 'quiz', 'quiz'])
        self.assertEqual([s.position for s in steps], list(range(1, 9)))
        self.assertEqual([s.title for s in steps], [p['title'] for p in plan])
        self.assertFalse(any(s.published for s in steps))
        self.assertEqual(steps[-1].quiz_mode, 'multiple')
        self.assertEqual(steps[-2].options, ['Верно', 'Неверно'])
        # Newly created empty slides still expose the material palette.
        page = self.client.get(reverse('step_editor', args=[lesson.pk, steps[5].pk]))
        self.assertContains(page, 'data-block-palette')
        self.assertContains(page, 'data-assessment-panel="answer numeric"')

    def test_empty_plan_is_authoritative_and_updating_settings_never_recreates_slides(self):
        self.client.post(self.lesson_url(), {'title': 'Пустой по выбору', 'minutes': 20, 'slide_plan': '[]', 'template': 'standard'})
        lesson = Lesson.objects.get(title='Пустой по выбору')
        self.assertFalse(lesson.steps.exists())
        original = Step.objects.create(lesson=lesson, title='Свой слайд', published=False)
        self.client.post(reverse('lesson_editor', args=[self.section.pk, lesson.pk]), {
            'title': 'Переименован', 'minutes': 40, 'slide_plan': '[{"type":"quiz","title":"Лишний"}]'})
        self.assertEqual(list(lesson.steps.values_list('pk', flat=True)), [original.pk])

    def test_invalid_plans_are_atomic_and_retain_input(self):
        original_counts = (Lesson.objects.count(), Step.objects.count())
        for value in ['{bad', '{}', '[null]', '[{"type":[]}]', '[{"type":"unknown"}]',
                      '[{"type":"text","title":3}]', json.dumps([{'type': 'text', 'title': 'x'*181}]),
                      json.dumps([{'type': 'text'}]*61), json.dumps([{'type': 'text', 'title': '\x00'}])]:
            with self.subTest(value=value[:50]):
                response = self.client.post(self.lesson_url(), {'title': 'Не потерять название', 'minutes': 20, 'slide_plan': value})
                self.assertEqual(response.status_code, 200)
                self.assertIn('slide_plan', response.context['form'].errors)
                self.assertContains(response, 'Не потерять название')
                self.assertEqual(original_counts, (Lesson.objects.count(), Step.objects.count()))

    def test_another_teacher_cannot_create_or_change_slides(self):
        self.client.force_login(self.other)
        response = self.client.post(self.lesson_url(), {'title': 'Чужой', 'minutes': 20, 'slide_plan': '[{"type":"quiz"}]'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get(self.editor_url()).status_code, 404)
        self.assertFalse(Step.objects.exists())

    def test_theory_media_and_python_practice_work_on_same_slide(self):
        buf = io.BytesIO()
        Image.new('RGB', (4, 4), 'blue').save(buf, format='PNG')
        rows = [{'kind': 'text', 'text': '<h2>Степени двойки</h2><p><strong>Теория</strong></p>'},
                {'kind': 'image', 'image': SimpleUploadedFile('bits.png', buf.getvalue(), content_type='image/png')},
                {'kind': 'video', 'video': SimpleUploadedFile('lesson.mp4', b'test-video')},
                {'kind': 'text', 'text': '<p>Теперь вычислите 2 в десятой степени.</p>'}]
        step = self.save_slide(rows, kind='answer', answer='1024', code_mode='optional')
        self.client.force_login(self.student)
        url = reverse('learn_step', args=[step.pk])
        page = self.client.get(url)
        self.assertContains(page, '<strong>Теория</strong>')
        self.assertContains(page, '<video')
        self.assertContains(page, 'name="answer"')
        self.assertContains(page, 'name="python_code"')
        self.assertNotContains(page, 'value="1024"')
        self.client.post(url, {'action': 'answer', 'answer': '1024', 'python_code': 'print(2 ** 10)',
                               'submit_token': page.context['submit_token']})
        self.assertTrue(StepProgress.objects.get(user=self.student, step=step).completed)
        self.assertEqual(StepSubmission.objects.get(step=step).python_code, 'print(2 ** 10)')

    def test_switch_existing_theory_to_quiz_preserves_material_and_invalidates_old_completion(self):
        step = self.save_slide([{'kind': 'text', 'text': '<p>Теория с <u>форматированием</u></p>'}])
        block = step.content_blocks.get()
        StepProgress.objects.create(user=self.student, step=step, completed=True, revision=step.revision)
        page = self.client.get(self.editor_url(step))
        self.assertContains(page, '<select name="kind"')
        self.assertContains(page, 'options-TOTAL_FORMS')
        step = self.save_slide([{'id': block.pk, 'kind': 'text', 'text': block.text}], step,
            kind='quiz', quiz_mode='single', **{'options-TOTAL_FORMS': 2, 'options-INITIAL_FORMS': 0,
                'options-0-text': 'Восемь', 'options-0-correct': 'on', 'options-1-text': 'Девять'})
        self.assertEqual(step.revision, 2)
        self.assertEqual(step.content_blocks.get().pk, block.pk)
        self.assertEqual(step.content_blocks.get().text, block.text)
        self.assertEqual(step.correct_choices, [0])
        self.client.force_login(self.student)
        page = self.client.get(reverse('learn_step', args=[step.pk]))
        self.assertContains(page, '<u>форматированием</u>')
        self.assertContains(page, 'Восемь')
        self.assertNotIn(step.pk, page.context['completed'])
        self.client.post(reverse('learn_step', args=[step.pk]), {'action': 'answer', 'choice': '0', 'submit_token': page.context['submit_token']})
        self.assertTrue(StepSubmission.objects.get(step=step).correct)

    def test_switch_to_material_ignores_inactive_answers_and_clears_assessment(self):
        step = self.save_slide([{'kind': 'text', 'text': '<p>Материал остаётся</p>'}], kind='answer', answer='42', code_mode='required')
        block = step.content_blocks.get()
        step = self.save_slide([{'id': block.pk, 'kind': 'text', 'text': block.text}], step,
            kind='text', answer='x'*300, points='bad', max_attempts='bad', pairs_text='bad',
            tolerance='bad', pass_percent='bad', code_mode='bad', **{'options-TOTAL_FORMS': 'bad'})
        self.assertEqual((step.answer, step.options, step.pairs, step.code_mode, step.points), ('', [], [], 'off', 0))
        self.assertEqual(step.content_blocks.get().text, block.text)
        self.client.force_login(self.student)
        page = self.client.get(reverse('learn_step', args=[step.pk]))
        self.assertContains(page, 'Материал остаётся')
        self.assertNotContains(page, 'name="python_code"')
        self.assertNotContains(page, 'class="step-answer"')

    def test_new_quiz_without_extra_material_and_invalid_answer_edit_is_atomic(self):
        options = {'options-TOTAL_FORMS': 2, 'options-INITIAL_FORMS': 0, 'options-0-text': 'A',
                   'options-1-text': 'B', 'options-1-correct': 'on'}
        step = self.save_slide([], kind='quiz', title='Выберите B', quiz_mode='single', **options)
        self.assertFalse(step.content_blocks.exists())
        data = self.content_data([], step, kind='answer', answer='', title='Не сохранять')
        response = self.client.post(self.editor_url(step), data)
        self.assertEqual(response.status_code, 200)
        self.assertIn('answer', response.context['form'].errors)
        step.refresh_from_db()
        self.assertEqual((step.kind, step.title, step.options), ('quiz', 'Выберите B', ['A', 'B']))
