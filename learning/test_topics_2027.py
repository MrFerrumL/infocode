from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from learning.management.commands.seed_demo import example
from learning.models import Practice, Task, Topic
from learning.sources import polyakov_header_number
from learning.topic_defaults import TOPICS, TOPICS_2026


class Numbering2027Tests(SimpleTestCase):
    def test_headings_follow_ege_2027(self):
        self.assertEqual((TOPICS[9], TOPICS[12], TOPICS[22]), ('IP-адреса и маски сетей', 'Подсчёт программ', 'Алгоритмы на графах'))
        self.assertNotIn('Поиск в текстовых документах', TOPICS)

    def test_demo_examples_match_their_numbers(self):
        self.assertIn('маска сети', example(10, 1)[0])
        self.assertIn('последовательностей команд', example(13, 1)[0])
        statement, answer, solution, attachment = example(23, 1)
        self.assertIn('ациклический ориентированный взвешенный граф', statement)
        self.assertEqual(answer, '8')  # 1→2→4→5→6: 3 + 2 + 1 + 2
        self.assertEqual(attachment.splitlines()[0], '1 2 3')

    def test_demo_examples_are_not_retired(self):
        from learning.management.commands.retire_old_formats import NEW_FORMAT
        for number, (_, pattern) in NEW_FORMAT.items():
            for k in (1, 2, 3):
                self.assertRegex(example(number, k)[0], pattern)
        self.assertEqual(example(12, 2)[1], '3')

    def test_polyakov_header_number(self):
        self.assertEqual(polyakov_header_number('© К. Поляков, 2009-2026 13 (повышенный уровень, время – 8 мин) Тема: динамическое'), 13)
        self.assertEqual(polyakov_header_number('© К. Поляков, 2009-2026 10 (базвый уровень, время – 3 мин)'), 10)
        self.assertEqual(polyakov_header_number('2009-2026 7-2 (базовый уровень'), 7)
        self.assertIsNone(polyakov_header_number('Задачи 19-21: теория игр'))


class RetitleTopicsTests(TestCase):
    def setUp(self):
        self.topics = {n: Topic.objects.create(number=n, title=title) for n, title in TOPICS_2026.items()}
        self.ip = [self.task(13, f'IP-адреса и маски сетей · практика {k}') for k in (1, 2, 3)]
        self.search = [self.task(10, f'Поиск в текстовых документах · практика {k}') for k in (1, 2, 3)]
        self.bank = Task.objects.create(topic=self.topics[13], title='К. Поляков 13.77', answer='5', source_system='polyakov', external_id=77)
        self.pupil = get_user_model().objects.create_user('pupil')
        Practice.objects.create(user=self.pupil, task=self.ip[0], answer='1', correct=True)
        Practice.objects.create(user=self.pupil, task=self.search[0], answer='3', correct=True)

    def task(self, number, title):
        return Task.objects.create(topic=self.topics[number], title=title, answer='1', tags='вводный')

    def run_command(self, *args):
        out = StringIO()
        call_command('retitle_topics_2027', *args, stdout=out)
        return out.getvalue()

    def titles(self):
        return {t.number: t.title for t in Topic.objects.filter(number__in=TOPICS_2026)}

    def test_dry_run_changes_nothing(self):
        out = self.run_command('--dry-run')
        self.assertIn('«Поиск в текстовых документах» → «IP-адреса и маски сетей»', out)
        self.assertIn('№13 → №10: 3', out)
        self.assertEqual(self.titles(), TOPICS_2026)
        self.assertEqual(Task.all_objects.filter(topic=self.topics[13], title__startswith='IP').count(), 3)
        self.assertTrue(all(t.in_catalog for t in Task.all_objects.all()))

    def test_moves_ip_practice_and_hides_text_search(self):
        self.run_command()
        self.assertEqual(self.titles(), {10: 'IP-адреса и маски сетей', 13: 'Подсчёт программ', 23: 'Алгоритмы на графах'})
        for task in self.ip:
            task.refresh_from_db()
            self.assertEqual((task.topic.number, task.in_catalog), (10, True))
        for task in self.search:
            task.refresh_from_db()
            self.assertEqual((task.topic.number, task.in_catalog, task.published, task.tags), (10, False, True, 'вводный, старый-формат'))
        self.bank.refresh_from_db()
        self.assertEqual((self.bank.topic.number, self.bank.external_id, self.bank.in_catalog), (13, 77, True))
        self.assertEqual(Practice.objects.filter(user=self.pupil).count(), 2)
        self.assertEqual(self.run_command().count('already'), 3)  # a second run changes nothing

    def test_copy_already_in_ten_is_hidden_and_own_heading_kept(self):
        self.task(10, 'IP-адреса и маски сетей · практика 1')
        Topic.objects.filter(number=23).update(title='Графы: мой раздел')
        out = self.run_command()
        self.assertIn('own heading «Графы: мой раздел» kept', out)
        self.ip[0].refresh_from_db()
        self.assertEqual((self.ip[0].topic.number, self.ip[0].in_catalog), (13, False))
        self.assertEqual(Task.all_objects.filter(topic=self.topics[10], title__startswith='IP', in_catalog=True).count(), 3)

    def test_reverse_restores_2026(self):
        self.run_command()
        self.run_command('--reverse')
        self.assertEqual(self.titles(), TOPICS_2026)
        for task in self.ip + self.search:
            task.refresh_from_db()
            self.assertTrue(task.in_catalog)
            self.assertEqual(task.tags, 'вводный')
        self.assertEqual({t.topic.number for t in self.ip}, {13})
        self.assertEqual({t.topic.number for t in self.search}, {10})
