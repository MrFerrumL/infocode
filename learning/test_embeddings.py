import gzip
import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch
import numpy
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from . import embeddings
from .models import Task, TaskVector, Topic
from .similar_tasks import similar_to

# A stand-in for the model: a few words decide the direction of the vector.
AXES = ['исполнитель', 'простых', 'маска', 'файл']


def fake_embed(texts):
    rows = []
    for text in texts:
        vector = numpy.array([1.0 + 3 * (word in text.lower()) for word in AXES] + [0.0] * (embeddings.DIMENSIONS - len(AXES)), dtype=numpy.float32)
        rows.append(vector / numpy.linalg.norm(vector))
    return numpy.vstack(rows)


@patch.object(embeddings, 'available', return_value=True)
@patch.object(embeddings, 'embed', side_effect=fake_embed)
class EmbeddingTests(TestCase):
    def setUp(self):
        self.topic = Topic.objects.create(number=23, title='Исполнители')
        # Different answers: an equal text with the same answer would count as a copy of the task.
        make = lambda title, text, **kw: Task.objects.create(topic=self.topic, title=title, statement=text, answer=f'{title}-answer',
                                                             published=True, in_catalog=True, **kw)
        self.a = make('a', 'Исполнитель превращает число 3 в число 20. Сколько программ?')
        self.b = make('b', 'Исполнитель Альфа: сколько программ переводят 2 в 30?')
        self.c = make('c', 'Определите количество простых чисел в диапазоне от 2 до 20000.')
        self.picture = make('picture', '', source_system='kompege', external_id=777)

    def run_embed(self, *args):
        out = StringIO()
        call_command('embed_tasks', *args, stdout=out)
        return out.getvalue()

    def test_only_new_or_changed_texts_are_embedded(self, embed, _):
        with tempfile.TemporaryDirectory() as folder:
            texts = Path(folder) / 'texts.json.gz'
            texts.write_bytes(gzip.compress(json.dumps({'kompege:23:777': 'Исполнитель с файлом команд: сколько программ?',
                                                        'kompege:23:999': 'Нет такой задачи'}).encode()))
            self.assertIn('Source texts: 1 of 2', self.run_embed('--texts', str(texts)))
        self.assertEqual(TaskVector.objects.exclude(vector=None).count(), 4)  # the picture-only task got its source words
        self.assertIn('New or changed vectors: 0', self.run_embed())
        Task.objects.filter(pk=self.c.pk).update(statement='Найдите маску подсети для узла.')
        self.assertIn('New or changed vectors: 1', self.run_embed())

    def test_similar_tasks_by_meaning(self, embed, _):
        self.run_embed()
        user = get_user_model().objects.create_user('pupil')
        ids, how = similar_to(self.a, user, count=2)
        self.assertEqual((how, ids[0]), ('model', self.b.pk))  # the other executor task comes first
        self.assertNotIn(self.a.pk, ids)
        # A task without a vector gets it on the spot from its words.
        TaskVector.objects.filter(task=self.c).delete()
        ids, how = similar_to(self.c, user, count=2)
        self.assertEqual(how, 'model')
        self.assertTrue(TaskVector.objects.filter(task=self.c).exclude(vector=None).exists())
        # Without the model the old way still works: tasks of the same number.
        TaskVector.objects.filter(task=self.c).delete()
        with patch.object(embeddings, 'available', return_value=False):
            ids, how = similar_to(self.c, user, count=2)
        self.assertEqual(how, 'number')
        self.assertTrue(set(ids) <= {self.a.pk, self.b.pk, self.picture.pk})

    def test_tasks_without_words_are_skipped(self, embed, _):
        self.assertIn('pictures only, no source text): 1', self.run_embed())

    def test_tasks_of_variants_outside_the_catalogue(self, embed, _):
        from .models import Variant, VariantItem
        hidden = Task.objects.create(topic=self.topic, title='kim', statement='Исполнитель из варианта КИМ', answer='5',
                                     published=False, in_catalog=False)
        loose = Task.objects.create(topic=self.topic, title='loose', statement='Исполнитель вне вариантов', answer='6', in_catalog=False)
        teacher = get_user_model().objects.create_user('olga', is_staff=True)
        variant = Variant.objects.create(title='КИМ', creator=teacher)
        VariantItem.objects.create(variant=variant, task=hidden, position=1, number=23, statement=hidden.statement, answer='5')
        self.run_embed()
        self.assertTrue(TaskVector.objects.filter(task=hidden).exclude(vector=None).exists())
        self.assertFalse(TaskVector.objects.filter(task=loose).exists())


@patch.object(embeddings, 'available', return_value=True)
@patch.object(embeddings, 'embed', side_effect=fake_embed)
class CopiesTests(TestCase):
    def test_the_same_task_from_another_bank_is_not_similar(self, *_):
        topic = Topic.objects.create(number=23, title='Исполнители')
        make = lambda text, answer, **kw: Task.objects.create(topic=topic, title='t', statement=text, answer=answer, published=True, in_catalog=True, **kw)
        task = make('Исполнитель превращает число 3 в число 20. Сколько программ?', '42')
        copy = make('Исполнитель превращает число 3 в число 20. Сколько программ?', '42', source_system='polyakov', external_id=1)
        other = make('Исполнитель Альфа: сколько программ переводят 2 в 30?', '7')
        call_command('embed_tasks', stdout=StringIO())
        ids, how = similar_to(task, get_user_model().objects.create_user('p'), count=2)
        self.assertEqual(how, 'model')
        self.assertNotIn(copy.pk, ids)
        self.assertIn(other.pk, ids)


@patch.object(embeddings, 'available', return_value=True)
@patch.object(embeddings, 'embed', side_effect=fake_embed)
class RankingTests(TestCase):
    """A similar task is the closest by meaning among tasks of the same kind: a data file, the shape of the answer."""

    def setUp(self):
        self.topic = Topic.objects.create(number=24, title='Файлы')

    def make(self, text, answer, **kw):
        return Task.objects.create(topic=self.topic, title='t', statement=text, answer=answer, published=True, in_catalog=True, **kw)

    def similar(self, task, count):
        from .similar_tasks import by_vector
        call_command('embed_tasks', stdout=StringIO())
        return by_vector(task, Task.objects.all(), [24], count)

    def test_a_task_with_a_file_gets_tasks_with_files(self, *_):
        task = self.make('Исполнитель читает файл и считает', '5', attachment='tasks/a.txt')
        self.make('Исполнитель читает файл и считает строки', '6')  # closer, but without a file
        with_file = self.make('Определите по данным в файл количество простых', '7', attachment='tasks/b.txt')
        self.assertEqual(self.similar(task, 1), [with_file.pk])

    def test_the_same_shape_of_answer_and_no_near_copies(self, *_):
        task = self.make('Исполнитель: найдите два числа', '1 2')
        one = self.make('Исполнитель: найдите число', '9')  # as close, but one number in the answer
        two = self.make('Исполнитель — найдите два значения', '3 4')
        twin = self.make('Исполнитель, найдите два значения!', '5 6')  # the same text as «two» for the model
        other = self.make('Определите количество простых чисел', '7 8')
        picked = self.similar(task, 2)
        self.assertEqual(len(picked), 2)
        self.assertIn(picked[0], {two.pk, twin.pk})
        self.assertEqual(picked[1], other.pk)  # not the near-copy of the first, not the one-number answer
        self.assertNotIn(one.pk, picked)
