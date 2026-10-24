from django.contrib.auth.models import User
from django.test import TestCase
from .forms import StepForm
from .models import Course, Section, Lesson, Step, Enrollment, StepProgress, Practice, Task, TaskBlock, Topic
from .security import form_token


class BankTaskStepTests(TestCase):
    """A course slide can hold a task from the bank: same condition, same check, counted in the catalogue."""

    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user('bank-author', is_staff=True)
        cls.student = User.objects.create_user('bank-student')
        topic, _ = Topic.objects.get_or_create(number=8, defaults={'title': 'Комбинаторика'})
        cls.task = Task.objects.create(topic=topic, title='Слова из букв', statement='Сколько слов из 3 букв А, Б?', answer='8',
                                       solution='Каждая позиция — 2 варианта: 2·2·2.', published=True)
        TaskBlock.objects.create(task=cls.task, purpose='solution', kind='text', text='Правило произведения.')
        course = Course.objects.create(title='Курс', owner=cls.teacher, published=True)
        lesson = Lesson.objects.create(section=Section.objects.create(course=course, title='№ 8'), title='Урок', published=True)
        cls.step = Step.objects.create(lesson=lesson, title='Задача из банка', kind='task', task=cls.task, points=1)
        Enrollment.objects.create(user=cls.student, course=course)

    def answer(self, value):
        return self.client.post(f'/learn/{self.step.pk}/', {'action': 'answer', 'answer': value,
                                                             'submit_token': form_token(self.student, f'step:{self.step.pk}')})

    def test_pupil_sees_the_bank_condition_and_solves_it(self):
        self.client.force_login(self.student)
        page = self.client.get(f'/learn/{self.step.pk}/')
        self.assertContains(page, 'Сколько слов из 3 букв')
        self.assertContains(page, f'ID {self.task.pk}')
        self.assertNotContains(page, 'Правило произведения')  # the solution waits for a right answer
        self.answer('6')
        self.assertFalse(StepProgress.objects.get(user=self.student, step=self.step).completed)
        self.answer(' 8 ')
        progress = StepProgress.objects.get(user=self.student, step=self.step)
        self.assertTrue(progress.completed)
        self.assertEqual(progress.points, 1)
        # Both attempts are in the catalogue history, so daily practice and analytics see them.
        self.assertEqual(list(Practice.objects.filter(user=self.student, task=self.task).order_by('id').values_list('correct', flat=True)), [False, True])
        page = self.client.get(f'/learn/{self.step.pk}/')
        self.assertContains(page, 'Ответ и разбор из банка')
        self.assertContains(page, 'Правило произведения')

    def test_unpublished_task_is_not_offered(self):
        Task.objects.filter(pk=self.task.pk).update(published=False)
        self.client.force_login(self.student)
        page = self.client.get(f'/learn/{self.step.pk}/')
        self.assertContains(page, 'сейчас недоступна')
        self.assertNotContains(page, 'Сколько слов из 3 букв')
        self.assertEqual(self.answer('8').status_code, 403)
        self.assertFalse(Practice.objects.exists())

    def test_teacher_picks_a_task_by_id(self):
        data = {'title': 'Практика', 'kind': 'task', 'task': str(self.task.pk), 'points': '1', 'max_attempts': '0', 'published': 'on'}
        form = StepForm(data, instance=Step(lesson=self.step.lesson, position=2))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().task, self.task)
        form = StepForm(dict(data, task='999999'), instance=Step(lesson=self.step.lesson, position=3))
        self.assertFalse(form.is_valid())
        self.assertIn('не найдена', str(form.errors['task']))
        # Another kind drops a stale task id left in the browser draft.
        form = StepForm(dict(data, kind='answer', answer='5'), instance=Step(lesson=self.step.lesson, position=4))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.save().task)


class SeedCourseTests(TestCase):
    """seed_course_inf builds the course from learning/data/course_inf and can run again without losing progress."""

    def setUp(self):
        from django.core.management import call_command
        self.call = lambda: call_command('seed_course_inf', stdout=__import__('io').StringIO())
        User.objects.create_superuser('seed-admin', password='x')
        for number in range(1, 6):
            topic, _ = Topic.objects.get_or_create(number=number, defaults={'title': f'№ {number}'})
            for n, (level, kind) in enumerate([('easy', 'demo'), ('easy', 'exam'), ('medium', 'exam'), ('medium', 'author'), ('hard', 'author'), ('easy', '')]):
                Task.objects.create(topic=topic, title=f'{number}.{n}', statement='Условие', answer=str(n), difficulty=level,
                                    source_kind=kind, published=True, external_id=100 + n)

    def test_course_is_built_and_rebuilt_safely(self):
        from learning.management.commands.seed_course_inf import TITLE
        self.call()
        course = Course.objects.get(title=TITLE)
        self.assertFalse(course.published)          # a draft until the teacher publishes it
        steps = Step.objects.filter(lesson__section__course=course)
        bank = steps.filter(kind='task')
        self.assertEqual(bank.count(), 25)
        self.assertEqual(bank.values('task').distinct().count(), 25)   # no task twice in the course
        warmup = bank.get(lesson__section__order=1, title='Разминка')
        self.assertEqual((warmup.task.difficulty, warmup.task.source_kind), ('easy', 'demo'))
        theory = steps.get(lesson__section__order=2, kind='text')
        self.assertTrue(theory.use_blocks)
        self.assertIn('code', list(theory.content_blocks.values_list('kind', flat=True)))
        quiz = steps.get(lesson__section__order=4, kind='quiz')
        self.assertEqual(quiz.options[quiz.correct_choices[0]], '0, 10, 110, 111')
        self.assertEqual(steps.get(lesson__section__order=5, kind='answer').answer, '16')
        pupil = User.objects.create_user('seed-pupil')
        StepProgress.objects.create(user=pupil, step=warmup, revision=warmup.revision, completed=True, points=1)
        self.call()
        warmup.refresh_from_db()
        self.assertEqual(Step.objects.filter(lesson__section__course=course).count(), steps.count())
        self.assertEqual(warmup.revision, 1)        # nothing changed, so the pupil's step stays done
        # An unpublished task is replaced and the step gets a new revision.
        old = warmup.task
        Task.objects.filter(pk=old.pk).update(published=False)
        self.call()
        warmup.refresh_from_db()
        self.assertNotEqual(warmup.task, old)
        self.assertEqual(warmup.revision, 2)
