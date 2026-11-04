from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from .models import Task, TaskVector, Topic
from .word_search import search, stem, words_of


class WordsTests(SimpleTestCase):
    def test_words_are_distinct_clean_and_limited(self):
        self.assertEqual(words_of('  Сумма,  «пар»  (остаток) сумма. '), ['Сумма', 'пар', 'остаток'])
        self.assertEqual(words_of(''), [])
        self.assertEqual(len(words_of(' '.join(f'слово{n}' for n in range(20)))), 8)


    def test_a_word_loses_its_endings_so_that_any_form_is_found(self):
        self.assertEqual([stem(w) for w in ('сумма', 'суммы', 'суммой', 'последовательность', 'восьмеричной', 'пар', 'F(n)', 'mod', '17')],
                         ['сумм', 'сумм', 'сумм', 'последовательност', 'восьмеричн', 'пар', 'f(n)', 'mod', '17'])


class SearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.eight, cls.five = Topic.objects.create(number=8, title='№ 8'), Topic.objects.create(number=5, title='№ 5')
        make = lambda topic, title, text='', **extra: Task.objects.create(topic=topic, title=title, statement=text, answer='1', **extra)  # noqa: E731
        cls.written = make(cls.eight, 'Числа', 'Определите количество пятизначных чисел, записанных в восьмеричной системе счисления.')
        cls.picture = make(cls.eight, 'Слова', '', source_system='kompege', external_id=777)  # a picture: its words are kept apart
        TaskVector.objects.create(task=cls.picture, source_text='Все пятибуквенные слова из букв А, В, Е, Р расположены в алфавитном порядке.', digest='x')
        cls.other = make(cls.eight, 'Четвёрки', 'Найдите количество четвёрок элементов.')
        cls.elsewhere = make(cls.five, 'Из пятёрки', 'Определите количество чисел в восьмеричной системе счисления.')

    def found(self, query, tasks=None):
        return {t.pk for t in search(tasks if tasks is not None else Task.objects.all(), query)}

    def test_every_word_in_any_order_and_any_form(self):
        self.assertEqual(self.found('пятизначных восьмеричной'), {self.written.pk})
        self.assertEqual(self.found('восьмеричной пятизначных'), {self.written.pk})  # the order does not matter
        self.assertEqual(self.found('восьмерич'), {self.written.pk, self.elsewhere.pk})  # a stem is enough
        self.assertEqual(self.found('пятизначных слова'), set())  # every word must be there

    def test_the_words_of_a_picture_condition_are_searched_too(self):
        self.assertEqual(self.found('пятибуквенные слова'), {self.picture.pk})
        self.assertEqual(self.found('АЛФАВИТНОМ порядке букв'), {self.picture.pk})  # capitals do not matter

    def test_any_form_of_a_word_is_found(self):
        self.assertEqual(self.found('количества числа'), {self.written.pk, self.elsewhere.pk})  # not the task about quadruples
        self.assertEqual(self.found('системе счисления'), {self.written.pk, self.elsewhere.pk})
        self.assertEqual(self.found('система счисление'), {self.written.pk, self.elsewhere.pk})  # the other form of the same words

    def test_e_and_yo_are_the_same_letter(self):
        self.assertEqual(self.found('четверок'), {self.other.pk})
        self.assertEqual(self.found('ЧЕТВЁРОК'), {self.other.pk})

    def test_a_number_also_finds_the_task_with_that_id(self):
        self.assertEqual(self.found(str(self.written.pk)), {self.written.pk})
        self.assertEqual(self.found('777'), {self.picture.pk})  # the ID in the source bank

    def test_the_number_filter_and_the_phrase_work_together_in_the_catalogue_and_the_pickers(self):
        student = get_user_model().objects.create_user('pupil', password='x')
        self.client.force_login(student)
        page = self.client.get('/catalog/?number=8&q=восьмеричной системе')
        self.assertEqual({t.pk for t in page.context['page']}, {self.written.pk})  # the same words in № 5 are not shown
        self.assertEqual({t.pk for t in self.client.get('/catalog/?number=8&q=слова порядке').context['page']}, {self.picture.pk})
        teacher = get_user_model().objects.create_user('teacher', password='x', is_staff=True)
        self.client.force_login(teacher)
        picker = self.client.get('/teacher/kr/tasks/?number=8&q=слова букв')
        self.assertContains(picker, f'data-task="{self.picture.pk}"')
        self.assertNotContains(picker, f'data-task="{self.written.pk}"')
        bank = self.client.get('/teacher/tasks/?q=пятибуквенные слова')
        self.assertContains(bank, 'Слова')
