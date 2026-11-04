from io import StringIO
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from . import seventeen
from .models import Task, TaskVector, Topic

PAIRS = 'В файле содержится последовательность целых чисел. Определите количество пар последовательности, в которых сумма делится на 3.'
TRIPLES = 'В файле содержится последовательность целых чисел. Определите количество троек элементов, в которых ровно два элемента из трёх нечётные.'
QUADS = 'В файле содержится последовательность целых чисел. Определите и запишите в ответе сначала количество четвёрок элементов последовательности.'
SINGLE = 'В файле содержится последовательность целых чисел. Определите количество элементов, которые делятся на 7. В ответе запишите пара чисел не нужна.'


class ClassifyTests(SimpleTestCase):
    def test_the_question_decides_not_any_mention(self):
        cases = [(PAIRS, '2'), (TRIPLES, '3'), (QUADS, '4'),
                 ('Назовём два числа неудачной парой, если они различаются в одном разряде. Найдите все тройки элементов последовательности.', '3'),
                 ('Среди четверок последовательности, в которых все числа оканчиваются на одну цифру, найдите наибольшую сумму.', '4'),
                 ('Определите количество элементов последовательности больших любой суммы пары элементов.', ''),
                 ('Найдите количество чисел и наибольшее число. Ответом будет пара чисел 2 и 4.', ''),
                 (SINGLE, ''), ('Параметр сети на троих компьютерах.', ''), ('', '')]
        for text, expected in cases:
            with self.subTest(text=text[:50]):
                self.assertEqual(seventeen.classify(text), expected)

    def test_a_teachers_own_subtopic_is_kept(self):
        self.assertIsNone(seventeen.subtopic_for(SimpleNamespace(subtopic='Мои пары'), PAIRS))
        self.assertEqual(seventeen.subtopic_for(SimpleNamespace(subtopic=''), PAIRS), 'Перебор пар')
        self.assertEqual(seventeen.subtopic_for(SimpleNamespace(subtopic='Перебор пар'), TRIPLES), 'Перебор троек')  # an automatic mark follows the rules
        self.assertEqual(seventeen.subtopic_for(SimpleNamespace(subtopic='Перебор пар'), SINGLE), '')


class Bank:
    @classmethod
    def build(cls):
        cls.topic = Topic.objects.create(number=17, title='№ 17')
        cls.other = Topic.objects.create(number=5, title='№ 5')
        make = lambda text, topic=None, **extra: Task.objects.create(topic=topic or cls.topic, title='t', statement=text, answer='1', **extra)  # noqa: E731
        cls.pairs, cls.pairs_more, cls.triples, cls.quads, cls.single = make(PAIRS), make(PAIRS + ' Ещё.'), make(TRIPLES), make(QUADS), make(SINGLE)
        cls.picture = make('', source_system='kompege', external_id=7)  # a picture: the words are kept with its vector
        TaskVector.objects.create(task=cls.picture, source_text=TRIPLES, digest='x')
        cls.five = make(PAIRS, topic=cls.other)  # the same words in another number are not № 17


class ClassifyCommandTests(Bank, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build()

    def run_command(self, *args):
        out = StringIO()
        call_command('classify_seventeen', *args, stdout=out)
        return out.getvalue()

    def subtopic(self, task):
        return Task.all_objects.get(pk=task.pk).subtopic

    def test_marks_by_the_question_including_pictures_with_source_words(self):
        Task.objects.filter(pk=self.single.pk).update(subtopic='Свои числа')
        report = self.run_command()
        self.assertIn('Tasks changed: 5', report)
        self.assertEqual([self.subtopic(t) for t in (self.pairs, self.pairs_more, self.triples, self.quads, self.picture)],
                         ['Перебор пар', 'Перебор пар', 'Перебор троек', 'Перебор четвёрок', 'Перебор троек'])
        self.assertEqual(self.subtopic(self.single), 'Свои числа')  # a teacher's subtopic stays
        self.assertEqual(self.subtopic(self.five), '')  # not № 17
        self.assertIn('Tasks changed: 0', self.run_command())  # nothing to do the second time

    def test_dry_run_saves_nothing_and_revert_removes_only_automatic_marks(self):
        self.assertIn('dry run', self.run_command('--dry-run'))
        self.assertEqual(self.subtopic(self.pairs), '')
        self.run_command()
        Task.objects.filter(pk=self.single.pk).update(subtopic='Свои числа')
        self.run_command('--revert')
        self.assertEqual([self.subtopic(t) for t in (self.pairs, self.triples, self.picture)], ['', '', ''])
        self.assertEqual(self.subtopic(self.single), 'Свои числа')


class FilterTests(Bank, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build()
        call_command('classify_seventeen', stdout=StringIO())
        cls.student = get_user_model().objects.create_user('pupil', password='x')
        cls.teacher = get_user_model().objects.create_user('teacher', password='x', is_staff=True)

    def test_catalogue_filters_and_counts_the_kinds(self):
        self.client.force_login(self.student)
        everything = self.client.get('/catalog/?number=17')
        chips = {c['label']: c['count'] for c in everything.context['tuple_chips']}
        self.assertEqual(chips, {'Любой перебор': 6, 'Двойки (пары)': 2, 'Тройки': 2, 'Четвёрки': 1})
        self.assertEqual(everything.context['page'].paginator.count, 6)
        for key, expected in (('2', {self.pairs, self.pairs_more}), ('3', {self.triples, self.picture}), ('4', {self.quads})):
            with self.subTest(kind=key):
                page = self.client.get(f'/catalog/?number=17&tuple={key}').context['page']
                self.assertEqual({t.pk for t in page}, {t.pk for t in expected})
        chosen = self.client.get('/catalog/?number=17&tuple=3&difficulty=easy')
        self.assertTrue(next(c for c in chosen.context['tuple_chips'] if c['label'] == 'Тройки')['on'])
        self.assertIn('difficulty=easy', next(c for c in chosen.context['tuple_chips'] if c['label'] == 'Четвёрки')['url'])  # the other filters stay
        self.assertEqual(self.client.get('/catalog/?number=5').context['tuple_chips'], [])  # the buttons exist for № 17 only
        self.assertEqual(self.client.get('/catalog/?number=17&tuple=9').context['page'].paginator.count, 6)  # an unknown kind is ignored

    def test_the_kr_picker_filters_the_same_way(self):
        self.client.force_login(self.teacher)
        page = self.client.get('/teacher/kr/tasks/?number=17&tuple=4')
        self.assertContains(page, f'data-task="{self.quads.pk}"')
        for other in (self.pairs, self.triples, self.single):
            self.assertNotContains(page, f'data-task="{other.pk}"')
        self.assertContains(self.client.get('/teacher/kr/tasks/?number=17'), f'data-task="{self.single.pk}"')  # without the filter all of them
