from io import StringIO
import numpy
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from . import exam_sources
from .embeddings import DIMENSIONS
from .models import Task, Topic


def blob(seed):
    vector = numpy.random.default_rng(seed).standard_normal(DIMENSIONS)
    return (vector / numpy.linalg.norm(vector)).astype(numpy.float16).tobytes()


class ClassifyTests(TestCase):
    """The bank's note or the bracket opening the condition tells where an exam task comes from."""

    def test_labels_and_brackets(self):
        cases = {('Основная волна 18.06.26', ''): 'main', ('Апробация 04.03.26', ''): 'approbation',
                 ('Резервный день 2025', ''): 'reserve', ('ЕГКР 2025', ''): 'egkr', ('Досрочная волна 2024', ''): 'early',
                 ('Пересдача 2025', ''): 'retake', ('Открытый вариант 2024', ''): 'open', ('Статград, март', ''): 'statgrad',
                 ('Демоверсия 2026', ''): 'demo', ('Открытый банк ФИПИ', ''): 'fipi',
                 ('', '(Апробация-2026) Исполнитель…'): 'approbation', ('', '3) (ЕГЭ-2025) Сколько…'): 'exam',
                 ('', '(Демо-2027) Текст'): 'demo', ('', 'Текст (ЕГЭ-2025) в середине'): '', ('Л. Шастин', ''): ''}
        for (label, text), code in cases.items():
            self.assertEqual(exam_sources.classify(label, text), code, (label, text))

    def test_years(self):
        cases = {('ЕГЭ-2024', ''): 2024, ('Апробация 04.03.26', ''): 2026, ('Основная волна 2022', ''): 2022,
                 ('Демоверсия 2027', ''): 2027, ('', '(ЕГЭ-2025) Сколько чисел больше 2024?'): 2025,
                 ('Л. Шастин', 'Найдите 2024-е число'): None, ('', 'Числа 12.05.24 и 2023'): None}
        for (label, text), year in cases.items():
            self.assertEqual(exam_sources.year_of(label, text), year, (label, text))

    def test_level_follows_the_source(self):
        task = Task(difficulty='hard')
        self.assertTrue(exam_sources.apply(task, 'demo'))
        self.assertEqual((task.difficulty, task.original_difficulty), ('ege', 'hard'))
        self.assertTrue(exam_sources.apply(task, 'main'))
        self.assertEqual((task.difficulty, task.original_difficulty), ('ege', 'hard'))  # still official: the own level is kept
        self.assertFalse(exam_sources.apply(task, 'main'))
        self.assertTrue(exam_sources.apply(task, 'statgrad'))  # not official: the own level comes back
        self.assertEqual((task.exam_source, task.difficulty, task.original_difficulty), ('statgrad', 'hard', ''))

    def test_copies_inherit_by_vector_and_answer(self):
        labelled = [(1, 5, '42', blob(1), 'demo'), (2, 5, '7', blob(2), 'main'), (3, 6, '42', None, 'egkr')]
        unlabelled = [(10, 5, ' 42 ', blob(1), ), (11, 5, '8', blob(2)), (12, 5, '42', blob(3)), (13, 6, '42', blob(4))]
        self.assertEqual(exam_sources.copies(unlabelled, labelled), {10: 'demo'})


class CommandTests(TestCase):
    def setUp(self):
        topic = Topic.objects.create(number=5, title='Алгоритмы')
        make = lambda **kw: Task.objects.create(topic=topic, title='З', answer='1', published=True, in_catalog=True, **kw)  # noqa: E731
        self.demo = make(source_label='Демоверсия 2026', difficulty='medium')
        self.bracket = make(statement='(Апробация-2026) Текст', difficulty='hard')
        self.author = make(source_label='Л. Шастин', difficulty='easy')
        self.manual = make(source_label='ЕГКР 2025', difficulty='grave', exam_source='statgrad', exam_source_manual=True)

    def states(self):
        return [Task.objects.filter(pk=t.pk).values_list('exam_source', 'difficulty', 'original_difficulty')[0]
                for t in (self.demo, self.bracket, self.author, self.manual)]

    def test_dry_run_run_and_revert(self):
        before = self.states()
        call_command('classify_exam_sources', '--dry-run', stdout=StringIO())
        self.assertEqual(self.states(), before)
        call_command('classify_exam_sources', stdout=StringIO())
        self.assertEqual(self.states(), [('demo', 'ege', 'medium'), ('approbation', 'ege', 'hard'), ('', 'easy', ''),
                                         ('statgrad', 'grave', '')])  # the teacher's choice stays
        years = dict(Task.objects.values_list('pk', 'exam_year'))
        self.assertEqual([years[t.pk] for t in (self.demo, self.bracket, self.author, self.manual)], [2026, 2026, None, 2025])
        call_command('classify_exam_sources', '--revert', stdout=StringIO())
        self.assertEqual(self.states(), before)


@override_settings(KR_API_TOKEN='secret', KR_PUBLIC_URL='/kr')
class TeacherSortingTests(TestCase):
    """The teacher moves a task to another level or source at once and filters the catalogue and the КР picker by it."""

    def setUp(self):
        users = get_user_model()
        self.teacher = users.objects.create_user('olga', password='x', is_staff=True)
        self.pupil = users.objects.create_user('pupil', password='x')
        topic = Topic.objects.create(number=5, title='Алгоритмы')
        self.task = Task.objects.create(topic=topic, title='Простая', statement='Условие один', answer='1', difficulty='medium',
                                        published=True, in_catalog=True)
        self.official = Task.objects.create(topic=topic, title='С экзамена', statement='Условие два', answer='2', difficulty='ege',
                                            original_difficulty='hard', exam_source='main', exam_year=2024, published=True, in_catalog=True)
        self.recent = Task.objects.create(topic=topic, title='Свежая', statement='Условие три', answer='3', difficulty='ege',
                                          exam_source='demo', exam_year=2026, published=True, in_catalog=True)

    def test_classify_endpoint(self):
        url = f'/teacher/tasks/{self.task.pk}/classify/'
        self.client.force_login(self.pupil)
        self.assertEqual(self.client.post(url, {'exam_source': 'demo'}).status_code, 302)
        self.client.force_login(self.teacher)
        reply = self.client.post(url, {'exam_source': 'demo'}).json()
        self.assertEqual((reply['exam_source'], reply['difficulty'], reply['difficulty_label']), ('demo', 'ege', 'ЕГЭ'))
        self.task.refresh_from_db()
        self.assertTrue(self.task.exam_source_manual)
        self.assertEqual(self.task.original_difficulty, 'medium')
        self.client.post(url, {'exam_source': 'statgrad'})
        self.task.refresh_from_db()
        self.assertEqual((self.task.exam_source, self.task.difficulty), ('statgrad', 'medium'))
        self.client.post(url, {'difficulty': 'grave'})
        self.task.refresh_from_db()
        self.assertEqual((self.task.exam_source, self.task.difficulty), ('statgrad', 'grave'))
        self.assertEqual(self.client.post(url, {'exam_source': 'nope'}).status_code, 400)
        self.assertEqual(self.client.post(url, {'difficulty': 'nope'}).status_code, 400)
        self.assertEqual(self.client.post('/teacher/tasks/999999/classify/', {'difficulty': 'ege'}).status_code, 404)

    def test_picker_and_catalog_filters(self):
        self.client.force_login(self.teacher)
        for params, shown, hidden in [({'source': 'official'}, 'С экзамена', 'Простая'), ({'source': 'main'}, 'С экзамена', 'Простая'),
                                      ({'level': 'medium'}, 'Простая', 'С экзамена'), ({}, 'Простая', None)]:
            page = self.client.get('/teacher/kr/tasks/', params)
            self.assertContains(page, shown)
            if hidden:
                self.assertNotContains(page, hidden)
        page = self.client.get('/catalog/', {'exam': 'official'})
        self.assertContains(page, 'С экзамена')
        self.assertNotContains(page, 'Простая')
        self.assertContains(page, 'data-classify')

    def test_builder_counts_by_level_and_source(self):
        self.client.force_login(self.teacher)
        from unittest.mock import patch
        from . import kr_client
        with patch.object(kr_client, 'tests_of', return_value=[]):
            page = self.client.get('/teacher/kr/')
        self.assertContains(page, 'id="kr-bank"')
        self.assertContains(page, '[5, "ege", "main", 2024, 1]')
        self.assertContains(page, 'data-bank-count')

    def test_year_filters(self):
        self.client.force_login(self.teacher)
        for params, shown, hidden in [({'year': '2026'}, 'Свежая', 'С экзамена'), ({'year': '2024'}, 'С экзамена', 'Свежая'),
                                      ({'year': '2025+'}, 'Свежая', 'С экзамена')]:
            page = self.client.get('/teacher/kr/tasks/', params)
            self.assertContains(page, shown)
            self.assertNotContains(page, hidden)
            self.assertNotContains(page, 'Простая')  # no known year
        page = self.client.get('/catalog/', {'year': '2026'})
        self.assertContains(page, 'Свежая')
        self.assertNotContains(page, 'С экзамена')
        self.assertContains(page, '2024 и новее')

    def test_builder_by_year(self):
        from unittest.mock import patch
        from . import kr_client
        self.client.force_login(self.teacher)
        with patch.object(kr_client, 'tests_of', return_value=[]):
            reply = self.client.post('/teacher/kr/', {'subject': 'inf', 'title': 'КР', 'variants': '1', 'duration': '45',
                                                      'year': '2025+', 'n5': '1', 'src5': 'bank'})
            self.assertRedirects(reply, '/teacher/kr/draft/', fetch_redirect_response=False)
            self.assertEqual(self.client.session['kr_draft']['variants'], [[self.recent.pk]])
            self.assertContains(self.client.get('/teacher/kr/draft/'), 'год: 2025 и новее')
            page = self.client.post('/teacher/kr/', {'subject': 'inf', 'title': 'КР', 'variants': '2', 'duration': '45',
                                                     'year': '2026', 'n5': '1', 'src5': 'bank'})
        self.assertContains(page, 'Для № 5 в банке 1 задач за 2026 год')
