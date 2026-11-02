import json
import random
import re
import tempfile
import zlib
from io import StringIO
from pathlib import Path
from unittest.mock import patch
import numpy
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from . import embeddings
from .kr_api import ComposeError
from .like_kr import auto_prototypes, compose_like
from .models import KrDraft, Task, TaskVector, Topic


def fake_embed(texts):
    """A stand-in for the model: every word sets one place of the vector, so texts alike in words are close."""
    rows = []
    for text in texts:
        vector = numpy.zeros(embeddings.DIMENSIONS, dtype=numpy.float32)
        for word in re.findall(r'\w+', text.lower()):
            vector[zlib.crc32(word.encode()) % embeddings.DIMENSIONS] += 1.0
        rows.append(vector / (numpy.linalg.norm(vector) or 1.0))
    return numpy.vstack(rows)


SEQUENCE = 'В файле содержится последовательность целых чисел пары элементов сумма остаток'
WORDS = 'Все пятибуквенные слова из букв алфавитном порядке номер слова'


class Bank:
    """Tasks of two kinds (a sequence in a file, five-letter words) in the banks КЕГЭ, К. Поляков and FIPI."""

    def __init__(self):
        self.topics = {n: Topic.objects.create(number=n, title=f'№ {n}') for n in (8, 17)}

    def add(self, number, text, system='kompege', official=False):
        task = Task.objects.create(topic=self.topics[number], title='t', statement=text, answer='1', published=True, in_catalog=True,
                                   source_system=system, external_id=Task.objects.count() + 1, exam_source='demo' if official else '')
        TaskVector.objects.create(task=task, vector=embeddings.to_bytes(fake_embed([text])[0]), digest=str(task.pk))
        return task


@patch.object(embeddings, 'embed', side_effect=fake_embed)
class ComposeLikeTests(TestCase):
    def setUp(self):
        self.bank = Bank()
        add = self.bank.add
        self.seq = [add(17, f'{SEQUENCE} вариант {k}', official=k < 3) for k in range(10)]
        self.words17 = [add(17, f'Найдите наибольший делитель маска сети вариант {k}') for k in range(10)]
        self.polyakov = [add(17, f'{SEQUENCE} отрезки вариант {k}', 'polyakov') for k in range(10)]
        self.words = [add(8, f'{WORDS} вариант {k}', official=k < 2) for k in range(10)]
        self.polyakov_words = [add(8, f'{WORDS} вариант {k}', 'polyakov') for k in range(10)]
        self.slots = [{'number': 17, 'text': SEQUENCE}, {'number': 8, 'text': WORDS}]
        self.sources = {'default': ['kompege'], '17': ['kompege']}

    def systems(self, ids):
        return set(Task.objects.filter(pk__in=ids).values_list('source_system', flat=True))

    def test_every_variant_gets_a_task_like_each_prototype_from_the_allowed_bank_only(self, _):
        variants, report = compose_like(self.slots, 3, self.sources, rng=random.Random(1))
        self.assertEqual([len(v) for v in variants], [2, 2, 2])
        flat = [i for v in variants for i in v]
        self.assertEqual(len(flat), len(set(flat)))  # no repeats
        self.assertEqual(self.systems(flat), {'kompege'})  # nothing of K. Polyakov, though his tasks are as close
        topics = Task.objects.filter(pk__in=flat).values_list('pk', 'topic__number', 'statement')
        for _, number, text in topics:
            self.assertIn('последовательность' if number == 17 else 'пятибуквенные', text)  # the right kind for the slot
        for v in variants:
            self.assertEqual(sorted(Task.objects.get(pk=i).topic.number for i in v), [8, 17])
        self.assertEqual(report['chosen'], 6)
        self.assertEqual(report['reused'], 0)

    def test_official_tasks_win_between_equally_close_ones(self, _):
        variants, report = compose_like(self.slots[:1], 3, self.sources, rng=random.Random(2))
        chosen = Task.objects.filter(pk__in=[i for v in variants for i in v])
        self.assertEqual(chosen.filter(exam_source='demo').count(), 3)  # the three official sequence tasks, not others

    def test_a_copy_of_the_prototype_is_not_taken(self, _):
        copy = self.bank.add(17, SEQUENCE, official=True)  # the very same condition as the prototype
        variants, report = compose_like(self.slots[:1], 3, self.sources, rng=random.Random(3))
        self.assertNotIn(copy.pk, [i for v in variants for i in v])
        self.assertGreaterEqual(report['copies_skipped'], 1)

    def test_a_prototype_that_is_a_task_of_the_bank_may_be_taken(self, _):
        slots = [{'number': 17, 'task': self.seq[0].pk}]
        variants, _ = compose_like(slots, 3, self.sources, rng=random.Random(4))
        self.assertIn(self.seq[0].pk, [i for v in variants for i in v])

    def test_tasks_of_other_drafts_go_last_and_banned_ones_never(self, _):
        used = {t.pk for t in self.seq[:8]}
        variants, report = compose_like(self.slots[:1], 3, self.sources, used=used, banned={self.seq[8].pk}, rng=random.Random(5))
        flat = [i for v in variants for i in v]
        self.assertNotIn(self.seq[8].pk, flat)
        self.assertEqual(report['reused'], 0)  # others of the same kind are left: seq[9] and other tasks
        self.assertIn(self.seq[9].pk, flat)
        # when nothing else is left the used ones are taken
        every = {t.pk for t in self.seq + self.words17}
        variants, report = compose_like(self.slots[:1], 3, self.sources, used=every, rng=random.Random(6))
        self.assertEqual(report['reused'], 3)

    def test_too_few_tasks_in_the_allowed_banks(self, _):
        with self.assertRaisesMessage(ComposeError, 'Для № 17 в разрешённых банках 20 задач с ответом, а нужно 60'):
            compose_like([{'number': 17, 'text': SEQUENCE}] * 20, 3, self.sources)

    def test_a_prototype_without_a_number_goes_to_the_number_it_resembles(self, _):
        slots = [{'number': 17, 'text': SEQUENCE}, {'number': 8, 'text': WORDS}, {'number': None, 'text': WORDS + ' вариант'}]
        variants, _ = compose_like(slots, 2, self.sources, rng=random.Random(7))
        self.assertEqual(sorted(Task.objects.get(pk=i).topic.number for i in variants[0]), [8, 8, 17])

    def test_auto_prototypes_are_official_first_and_unlike_each_other(self, _):
        picked = auto_prototypes(17, 2, ['kompege'], rng=random.Random(8))
        tasks = Task.objects.filter(pk__in=picked)
        self.assertEqual(tasks.filter(exam_source='demo').count(), 2)  # three official sequence tasks exist; two are taken
        self.assertEqual(len(set(picked)), 2)
        self.assertEqual(set(tasks.values_list('source_system', flat=True)), {'kompege'})
        with self.assertRaises(ComposeError):
            auto_prototypes(17, 50, ['kompege'])


@patch.object(embeddings, 'embed', side_effect=fake_embed)
class MakeLikeDraftsTests(TestCase):
    def setUp(self):
        self.teacher = get_user_model().objects.create_user('olga', password='x', is_staff=True)
        bank = Bank()
        for k in range(30):
            bank.add(17, f'{SEQUENCE} вариант {k}', official=k < 12)
            bank.add(17, f'{SEQUENCE} отрезки вариант {k}', 'polyakov', official=True)
            bank.add(8, f'{WORDS} вариант {k}', official=k < 12)
            bank.add(8, f'{WORDS} вариант {k}', 'polyakov', official=True)

    def run_plan(self, plan):
        out = StringIO()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'plan.json'
            path.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
            call_command('make_kr_drafts', '--user', 'olga', '--plan', str(path), stdout=out)
        return out.getvalue()

    def test_like_drafts_use_only_the_allowed_banks_and_share_official_tasks(self, _):
        sources = {'default': ['kompege'], '17': ['kompege']}
        report = self.run_plan([
            {'title': 'КР А', 'mode': 'like', 'numbers': {'8': 2, '17': 3}, 'variants': 4, 'sources': sources},
            {'title': 'КР Б', 'mode': 'like', 'numbers': {'17': 4}, 'variants': 4, 'sources': sources}])
        self.assertIn('Drafts made: 2', report)
        a, b = (KrDraft.objects.get(title=t) for t in ('КР А', 'КР Б'))
        self.assertEqual([len(v) for v in a.data['variants']], [5] * 4)
        self.assertEqual([len(v) for v in b.data['variants']], [4] * 4)
        ids_a = [i for v in a.data['variants'] for i in v]
        ids_b = [i for v in b.data['variants'] for i in v]
        for ids in (ids_a, ids_b):
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual(set(Task.objects.filter(pk__in=ids).values_list('source_system', flat=True)), {'kompege'})
        self.assertFalse(set(ids_a) & set(ids_b))  # nothing shared while there are enough tasks
        official_17 = lambda ids: Task.objects.filter(pk__in=ids, topic__number=17, exam_source='demo').count()  # noqa: E731
        # 12 official ones are dealt out 5 and 6 (12*12/28, 12*16/28); the one left over goes to the first draft
        self.assertEqual((official_17(ids_a), official_17(ids_b)), (6, 6))

    def test_without_prototypes_they_are_picked_from_the_numbers(self, _):
        self.run_plan([{'title': 'КР В', 'mode': 'like', 'numbers': {'17': 2}, 'variants': 3}])
        row = KrDraft.objects.get(title='КР В')
        self.assertEqual([len(v) for v in row.data['variants']], [2, 2, 2])
        self.assertEqual(row.data['source'], '')
