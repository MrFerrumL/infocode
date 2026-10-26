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
        # Without a vector the old way still works: tasks of the same number.
        TaskVector.objects.filter(task=self.c).delete()
        ids, how = similar_to(self.c, user, count=2)
        self.assertEqual(how, 'number')
        self.assertTrue(set(ids) <= {self.a.pk, self.b.pk, self.picture.pk})

    def test_tasks_without_words_are_skipped(self, embed, _):
        self.assertIn('pictures only, no source text): 1', self.run_embed())


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
