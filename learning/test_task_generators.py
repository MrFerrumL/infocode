import random
from io import StringIO
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from .models import Task, Topic
from .task_generators import REGISTRY, SYSTEM, Rejected, generate
from .task_generators.n13 import Paths, Results


class GeneratorTests(SimpleTestCase):
    """Each generator solves its tasks twice; the two ways must always agree."""

    def test_two_methods_agree_on_many_tasks(self):
        rng = random.Random(2026)
        for generator in [g for gens in REGISTRY.values() for g in gens]:
            made = 0
            for _ in range(400):
                p = generator.params(rng)
                try:
                    answer = generator.solve(p)
                except Rejected:
                    continue
                self.assertEqual(answer, generator.check(p), (generator.slug, p))
                made += 1
            self.assertGreater(made, 50, generator.slug)

    def test_known_answers(self):
        # +1, ×2 from 1 to 10, counted by hand: 1→1, 2→2 (1+1 and 1·2), 3→2, 4→4, 5→4, 6→6, 7→6, 8→10, 9→10, 10→14.
        p = {'name': 'Альфа', 'commands': [['add', 1], ['mul', 2]], 'start': 1, 'target': 10, 'through': None, 'avoid': None}
        self.assertEqual(Paths().check(p), 14)
        # Through 10: programs 1 → 10 times programs 10 → 20.
        self.assertEqual(Paths().solve(dict(p, target=20, through=10)), 14 * Paths().check(dict(p, start=10, target=20)))
        q = {'name': 'Альфа', 'commands': [['add', 1], ['mul', 2]], 'start': 1, 'length': 6}
        self.assertEqual(Results().solve(q), Results().check(q))

    def test_generate_is_varied_and_respects_the_bank(self):
        tasks = generate(13, 30, seed=7)
        self.assertEqual(len(tasks), 30)
        self.assertEqual(len({t.statement for t in tasks}), 30)
        self.assertEqual({t.kind for t in tasks}, {'13.paths', '13.results'})
        again = generate(13, 30, seed=7, taken={t.key for t in tasks})
        self.assertFalse({t.key for t in again} & {t.key for t in tasks})
        for task in tasks:
            self.assertIn('Ответ: ' + task.answer, task.solution)
            self.assertIn(task.difficulty, ('easy', 'medium', 'hard', 'ege'))


class CommandTests(TestCase):
    def setUp(self):
        Topic.objects.create(number=13, title='Динамическое программирование')

    def run_command(self, *args):
        out = StringIO()
        call_command('generate_tasks', *args, stdout=out)
        return out.getvalue()

    def test_drafts_without_repeats(self):
        self.assertIn('№ 13: 5 new tasks', self.run_command('--numbers', '13', '--count', '5', '--seed', '1', '--dry-run'))
        self.assertEqual(Task.all_objects.count(), 0)
        self.run_command('--thinnest', '1', '--count', '12', '--seed', '1')
        made = Task.all_objects.filter(source_system=SYSTEM)
        self.assertEqual((made.count(), made.filter(published=True).count()), (12, 0))
        task = made.first()
        self.assertEqual((task.source_label, task.source_kind, task.topic.number), ('Авторская · Инфокод', 'author', 13))
        # The same seed again gives only tasks that are not in the bank yet.
        self.run_command('--numbers', '13', '--count', '12', '--seed', '1', '--publish')
        self.assertEqual(made.count(), 24)
        self.assertEqual(made.values('external_id').distinct().count(), 24)
        self.assertEqual(made.filter(published=True).count(), 12)

    def test_unknown_number(self):
        with self.assertRaises(CommandError):
            self.run_command('--numbers', '5')
