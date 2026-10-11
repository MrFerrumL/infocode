from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Course, Section, Lesson, Step


class CourseCreationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user('creation-teacher', is_staff=True)
        cls.course = Course.objects.create(title='Курс', owner=cls.teacher)
        cls.section = Section.objects.create(title='Модуль', course=cls.course)

    def setUp(self):
        self.client.force_login(self.teacher)

    def test_unsaved_course_section_and_lesson_pages_do_not_create_records(self):
        before = (Course.objects.count(), Section.objects.count(), Lesson.objects.count(), Step.objects.count())
        for name, args, button in [
            ('course_create', [], 'Сохранить настройки'),
            ('section_create', [self.course.pk], 'Настройки модуля'),
            ('lesson_create', [self.section.pk], 'Сохранить урок'),
        ]:
            with self.subTest(page=name):
                response = self.client.get(reverse(name, args=args))
                self.assertContains(response, button)
                self.assertNotContains(response, 'Предпросмотр ↗')
        self.assertEqual(before, (Course.objects.count(), Section.objects.count(), Lesson.objects.count(), Step.objects.count()))

    def test_invalid_new_forms_redisplay_errors_without_creating_records(self):
        before = (Course.objects.count(), Section.objects.count(), Lesson.objects.count())
        for name, args, data in [
            ('course_create', [], {'title': ''}),
            ('section_create', [self.course.pk], {'title': ''}),
            ('lesson_create', [self.section.pk], {'title': 'Не терять этот текст', 'minutes': 'не число', 'template': 'standard'}),
        ]:
            with self.subTest(page=name):
                response = self.client.post(reverse(name, args=args), data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
                self.assertNotContains(response, 'Предпросмотр ↗')
                if name == 'lesson_create':
                    self.assertContains(response, 'Не терять этот текст')
        self.assertEqual(before, (Course.objects.count(), Section.objects.count(), Lesson.objects.count()))
        self.assertFalse(Step.objects.exists())

    def test_create_entire_course_and_open_all_step_types(self):
        response = self.client.post(reverse('course_create'), {'title': 'Новый полный курс'}, follow=True)
        self.assertEqual(response.status_code, 200)
        course = Course.objects.get(title='Новый полный курс')
        response = self.client.post(reverse('section_create', args=[course.pk]), {'title': 'Новый модуль'}, follow=True)
        self.assertEqual(response.status_code, 200)
        section = course.sections.get()
        response = self.client.post(reverse('lesson_create', args=[section.pk]), {'title': 'Новый урок', 'minutes': 20, 'template': ''}, follow=True)
        self.assertContains(response, 'Добавьте первый слайд: выберите карточку выше.')
        self.assertNotContains(response, 'Предпросмотр ↗')
        lesson = section.lessons.get()
        step_url = reverse('step_create', args=[lesson.pk])
        for kind, _ in Step.KINDS:
            with self.subTest(kind=kind):
                page = self.client.get(step_url, {'kind': kind})
                self.assertContains(page, 'Сохранить слайд')
                self.assertContains(page, 'data-content-blocks')
        self.assertFalse(lesson.steps.exists())
        response = self.client.post(step_url, {
            'title': 'Первый шаг', 'kind': 'text', 'points': 0, 'max_attempts': 0,
            'blocks-TOTAL_FORMS': 1, 'blocks-INITIAL_FORMS': 0,
            'blocks-0-kind': 'text', 'blocks-0-position': 1,
            'blocks-0-text': '<p><strong>Новая теория</strong></p>',
        }, follow=True)
        self.assertContains(response, 'Первый шаг')
        step = lesson.steps.get()
        self.assertFalse(step.published)
        self.assertContains(response, reverse('learn_step', args=[step.pk]))
        self.assertContains(self.client.get(reverse('learn_step', args=[step.pk])), '<strong>Новая теория</strong>')

    def test_lesson_templates_redirect_to_working_editor_and_preview(self):
        for template, kinds in [('standard', ['video', 'quiz', 'text', 'file']), ('practice', ['text', 'quiz', 'answer'])]:
            with self.subTest(template=template):
                response = self.client.post(reverse('lesson_create', args=[self.section.pk]), {
                    'title': template, 'minutes': 20, 'template': template,
                }, follow=True)
                self.assertEqual(response.status_code, 200)
                lesson = self.section.lessons.get(title=template)
                self.assertEqual(list(lesson.steps.values_list('kind', flat=True)), kinds)
                self.assertFalse(lesson.steps.filter(published=True).exists())
                self.assertContains(response, reverse('learn_step', args=[lesson.steps.first().pk]))

    def test_preview_uses_first_active_step_and_disappears_for_empty_lesson(self):
        lesson = Lesson.objects.create(section=self.section, title='Пустой', theory='')
        editor = reverse('lesson_editor', args=[self.section.pk, lesson.pk])
        self.assertNotContains(self.client.get(editor), 'Предпросмотр ↗')
        Step.objects.create(lesson=lesson, title='В корзине', position=0, published=False, archived_at=timezone.now())
        last = Step.objects.create(lesson=lesson, title='Второй', position=2, published=False)
        first = Step.objects.create(lesson=lesson, title='Первый', position=1, published=False)
        response = self.client.get(editor)
        self.assertContains(response, f'href="{reverse("learn_step", args=[first.pk])}"')
        self.assertNotContains(response, f'href="{reverse("learn_step", args=[last.pk])}"')
