import io
import tempfile
import zipfile
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from PIL import Image
from learning.collection_models import Collection
from learning.collections_builder import sync_all
from learning.models import Practice, Task, TaskBlock
from learning.security import form_token
from learning.yandex_import import import_collections, parse_answers


def picture(colour='white'):
    out = io.BytesIO()
    Image.new('RGB', (40, 20), colour).save(out, 'PNG')
    return out.getvalue()


class YandexImportTests(TestCase):
    """Hand-saved Yandex Uchebnik collections become catalogue tasks and on-site collections."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        override = self.settings(MEDIA_ROOT=self.root / 'media')
        override.enable()
        self.addCleanup(override.disable)
        folder = self.root / 'saved' / '№1 – знакомство'
        folder.mkdir(parents=True)
        (folder / '01.png').write_bytes(picture())
        (folder / '01-2.png').write_bytes(picture('black'))
        (folder / '02.png').write_bytes(picture('red'))
        (folder / '02-data.txt').write_text('1 2 3', encoding='utf-8')
        (folder / 'ответы.txt').write_text('1: 15\n2 12 -3\n', encoding='utf-8')
        self.folder = folder
        sync_all(size=10)

    def test_answer_lines(self):
        self.assertEqual(parse_answers('1: 15\n2) 3 4\n3 -5\nЗадание 4. 7', 'x'), {1: '15', 2: '3 4', 3: '-5', 4: '7'})
        with self.assertRaises(ValueError):
            parse_answers('пятнадцать', 'x')

    def test_import_builds_tasks_and_collection(self):
        collections, created, updated = import_collections(self.root / 'saved')
        self.assertEqual((collections, created, updated), (1, 2, 0))
        collection = Collection.objects.get(source='yandex', title='№ 1 — знакомство')
        tasks = [item.task for item in collection.items.all()]
        self.assertEqual([t.answer for t in tasks], ['15', '12 -3'])
        self.assertEqual({t.source_system for t in tasks}, {'yandex'})
        self.assertEqual(tasks[0].difficulty, 'easy')
        self.assertEqual(list(TaskBlock.objects.filter(task=tasks[0]).values_list('kind', flat=True)), ['image', 'image'])
        self.assertEqual(list(TaskBlock.objects.filter(task=tasks[1]).values_list('kind', flat=True)), ['image', 'file'])
        # A repeated import (e.g. a corrected answer) updates the same tasks and keeps pupils' answers.
        user = get_user_model().objects.create_user('pupil')
        Practice.objects.create(user=user, task=tasks[0], answer='15', correct=True)
        (self.folder / 'ответы.txt').write_text('1: 16\n2: 12 -3\n', encoding='utf-8')
        self.assertEqual(import_collections(self.root / 'saved'), (1, 0, 2))
        self.assertEqual(Task.objects.get(pk=tasks[0].pk).answer, '16')
        self.assertTrue(Practice.objects.filter(task=tasks[0]).exists())
        # Rebuilding own sets keeps the imported Yandex tasks in their collection.
        sync_all(size=10)
        self.assertEqual(collection.items.count(), 2)

    def test_zip_and_pupil_pages(self):
        archive = self.root / 'saved.zip'
        with zipfile.ZipFile(archive, 'w') as out:
            for path in self.folder.iterdir():
                out.write(path, f'Яндекс/{self.folder.name}/{path.name}')
        import_collections(archive)
        collection = Collection.objects.get(source='yandex', title='№ 1 — знакомство')
        other = Collection.objects.filter(source='yandex').exclude(pk=collection.pk).first()
        self.assertEqual(self.client.get('/collections/').status_code, 302)  # pupils only
        user = get_user_model().objects.create_user('pupil')
        self.client.force_login(user)
        page = self.client.get('/collections/')
        self.assertContains(page, f'/collections/{collection.pk}/')
        self.assertContains(page, other.url)  # not imported yet: still a link
        task = collection.items.first().task
        self.assertContains(self.client.get(f'/collections/{collection.pk}/'), f'id="task-{task.pk}"')
        self.assertEqual(self.client.get(f'/collections/{other.pk}/').status_code, 404)
        reply = self.client.post(f'/collections/{collection.pk}/', {'task_id': task.pk, 'submit_token': form_token(user, f'task:{task.pk}'),
                                 f'task-{task.pk}-answer': '15'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertTrue(reply.json()['solved'])

    def test_problems_are_reported_before_anything_is_saved(self):
        (self.folder / '03.png').write_bytes(picture())
        bad = self.root / 'saved' / '№ 99 — нет такой'
        bad.mkdir()
        (bad / 'ответы.txt').write_text('1: 1', encoding='utf-8')
        with self.assertRaises(CommandError) as error:
            call_command('import_yandex_collections', str(self.root / 'saved'), stdout=io.StringIO())
        self.assertIn('задача 3: нет ответа', str(error.exception))
        self.assertIn('№ 99 — нет такой', str(error.exception))
        self.assertFalse(Task.objects.filter(source_system='yandex').exists())
