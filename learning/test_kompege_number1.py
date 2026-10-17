import hashlib
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import zipfile
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from PIL import Image
from learning.models import Task, TaskBlock, Topic, Practice


class NumberOneImportTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        override = self.settings(MEDIA_ROOT=self.root / 'media')
        override.enable()
        self.addCleanup(override.disable)
        self.picture = io.BytesIO()
        Image.new('RGB', (1920, 120), 'white').save(self.picture, 'JPEG')
        self.blob = self.picture.getvalue()
        self.asset = 'screenshots/01-condition.jpg'
        self.manifest = {'format': 'infocode-kompege-number1-v1', 'source': 'https://kompege.ru',
            'source_count': 3, 'source_sha256': 'a' * 64, 'tasks': [
                {'number': 1, 'source_id': ident, 'source_url': f'https://kompege.ru/task?id={ident}',
                 'answer': '0012', 'difficulty': 'easy', 'author': 'Исходный автор', 'files': [],
                 'images': [{'path': self.asset, 'sha256': hashlib.sha256(self.blob).hexdigest()}]}
                for ident in (31498, 31347, 25)]}
        self.bundle = self.root / 'number1.zip'
        self.write_bundle()

    def write_bundle(self):
        with zipfile.ZipFile(self.bundle, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(self.manifest))
            archive.writestr(self.asset, self.blob)

    def run_import(self, **kwargs):
        output = io.StringIO()
        call_command('import_kompege_number1', str(self.bundle), stdout=output, **kwargs)
        return output.getvalue()

    def test_import_is_complete_and_repeat_is_idempotent(self):
        self.assertIn('created 3', self.run_import(publish=True))
        self.assertEqual(set(Task.objects.values_list('external_id', flat=True)), {31498, 31347, 25})
        self.assertEqual(Task.objects.filter(published=True, answer='0012').count(), 3)
        self.assertEqual(TaskBlock.objects.count(), 3)
        self.assertIn('created 0, skipped 3', self.run_import(publish=True))

    def test_existing_edits_archives_progress_and_other_topics_are_preserved(self):
        topic = Topic.objects.create(number=1, title='Мой заголовок')
        first = Task.all_objects.create(topic=topic, title='Правка', answer='9', source_system='kompege', external_id=31498, archived_at=timezone.now())
        TaskBlock.objects.create(task=first, text='Мой блок')
        other = Task.objects.create(topic=Topic.objects.create(number=2, title='Другой номер'), title='Авторское', answer='7')
        user = get_user_model().objects.create_user('bank-pupil')
        practice = Practice.objects.create(task=first, user=user, answer='9', correct=True)
        self.assertIn('created 2, skipped 1', self.run_import(publish=True))
        first.refresh_from_db()
        topic.refresh_from_db()
        other.refresh_from_db()
        practice.refresh_from_db()
        self.assertEqual((first.title, first.answer, topic.title, other.answer), ('Правка', '9', 'Мой заголовок', '7'))
        self.assertIsNotNone(first.archived_at)
        self.assertEqual(practice.task_id, first.pk)

    def test_dry_run_is_read_only(self):
        self.assertIn('Dry run', self.run_import(dry_run=True))
        self.assertFalse(Topic.objects.exists())
        self.assertFalse((self.root / 'media').exists())

    def test_bad_hash_low_resolution_and_unsafe_path_are_rejected(self):
        for mutation in ('hash', 'resolution', 'path'):
            with self.subTest(mutation=mutation):
                original = self.blob
                descriptor = self.manifest['tasks'][0]['images'][0]
                old = dict(descriptor)
                if mutation == 'hash':
                    descriptor['sha256'] = 'b' * 64
                elif mutation == 'resolution':
                    stream = io.BytesIO()
                    Image.new('RGB', (960, 120)).save(stream, 'JPEG')
                    self.blob = stream.getvalue()
                    descriptor['sha256'] = hashlib.sha256(self.blob).hexdigest()
                else:
                    descriptor['path'] = '../escape.jpg'
                self.write_bundle()
                with self.assertRaises(CommandError):
                    self.run_import()
                self.assertFalse(Task.all_objects.exists())
                self.blob = original
                descriptor.clear()
                descriptor.update(old)

    def test_wrong_type_duplicates_missing_answers_and_source_count_fail_closed(self):
        for key, value in (('number', 2), ('source_id', 31347), ('answer', '')):
            with self.subTest(key=key):
                row = self.manifest['tasks'][0]
                old = row[key]
                row[key] = value
                self.write_bundle()
                with self.assertRaises(CommandError):
                    self.run_import()
                row[key] = old
        self.manifest['source_count'] = 4
        self.write_bundle()
        with self.assertRaises(CommandError):
            self.run_import()
        self.assertFalse(Topic.objects.exists())

    def test_failure_rolls_back_database_and_new_media(self):
        with patch.object(TaskBlock, 'full_clean', side_effect=ValidationError('failed block')):
            with self.assertRaises(ValidationError):
                self.run_import()
        self.assertFalse(Task.all_objects.exists())
        self.assertFalse(Topic.objects.exists())
        self.assertFalse(any(p.is_file() for p in (self.root / 'media').rglob('*')))
