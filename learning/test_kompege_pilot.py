from collections import Counter
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
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from deploy.prepare_kompege_pilot import answer_text, safe_name, select_rows, source_url
from learning.models import Practice, Task, TaskBlock, Topic
from learning.services import create_variant


class KompegePilotTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.override = self.settings(MEDIA_ROOT=self.root / 'media')
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.teacher = get_user_model().objects.create_superuser('pilot-owner')
        self.student = get_user_model().objects.create_user('pilot-pupil')
        self.assets = {}
        for kind, suffix in (('PNG', 'png'), ('JPEG', 'jpg')):
            image = io.BytesIO()
            Image.new('RGB', (100, 60), 'white').save(image, format=kind)
            self.assets[f'screenshots/condition.{suffix}'] = image.getvalue()
        self.assets['attachments/table.ods'] = b'pilot attachment placeholder'
        self.manifest = {'format': 'infocode-kompege-pilot-v1', 'source': 'https://kompege.ru', 'rights': 'Licensed test fixture',
                         'topic_titles': {str(n): f'Тема источника {n}' for n in range(1, 28)}, 'tasks': []}
        for number in range(1, 28):
            for offset in range(5):
                source_id = (19 if number in (20, 21) else number) * 100 + offset
                self.manifest['tasks'].append({'number': number, 'source_id': source_id, 'source_url': f'https://kompege.ru/task?id={source_id}',
                    'answer': 'PRIVATE_PILOT_ANSWER', 'author': 'Автор исходника', 'difficulty': 'ege',
                    'images': [self.descriptor('screenshots/condition.png')] + ([self.descriptor('screenshots/condition.jpg')] if number in (20, 21) else []),
                    'files': [dict(self.descriptor('attachments/table.ods'), name='table.ods')] if number == 1 else []})
        self.bundle = self.root / 'pilot.zip'
        self.write_bundle()

    def descriptor(self, name):
        return {'path': name, 'sha256': hashlib.sha256(self.assets[name]).hexdigest()}

    def write_bundle(self):
        with zipfile.ZipFile(self.bundle, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(self.manifest))
            for name, value in self.assets.items():
                archive.writestr(name, value)

    def run_import(self, **options):
        output = io.StringIO()
        call_command('import_kompege_pilot', str(self.bundle), stdout=output, **options)
        return output.getvalue()

    def test_dry_run_does_not_create_topics_tasks_or_files(self):
        self.assertIn('Dry run', self.run_import(dry_run=True))
        self.assertEqual(Task.objects.count(), 0)
        self.assertEqual(Topic.objects.count(), 0)
        self.assertFalse((self.root / 'media').exists())

    def test_five_per_type_have_original_ids_images_files_and_hidden_answers(self):
        self.run_import(publish=True, sync_empty_topic_titles=True)
        self.assertEqual(Counter(Task.objects.values_list('topic__number', flat=True)), Counter({n: 5 for n in range(1, 28)}))
        self.assertEqual(Task.objects.filter(published=True).count(), 135)
        self.assertEqual(Topic.objects.get(number=10).title, 'Тема источника 10')
        self.assertEqual(Task.objects.filter(external_id=1900).count(), 3)
        task = Task.objects.get(topic__number=20, external_id=1900)
        self.assertEqual(task.content_blocks.filter(kind='image').count(), 2)
        self.client.force_login(self.student)
        page = self.client.get(reverse('task', args=[task.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'task-screenshots.css')
        self.assertContains(page, 'screenshot-task-layout')
        self.assertContains(page, task.source_url)
        self.assertContains(page, 'Вопрос задания 20')
        self.assertNotContains(page, 'PRIVATE_PILOT_ANSWER')
        block = TaskBlock.objects.filter(kind='file').first()
        response = self.client.get(reverse('download', args=['block', block.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), self.assets['attachments/table.ods'])
        self.assertIn('table.ods', response['Content-Disposition'])
        self.client.logout()
        self.assertEqual(self.client.get(reverse('download', args=['block', block.pk])).status_code, 302)

    def test_repeat_preserves_edits_archived_tasks_and_pupil_progress(self):
        self.run_import(publish=True)
        first = Task.objects.first()
        first.title, first.answer = 'Моя правка', '7'
        first.save()
        Practice.objects.create(user=self.student, task=first, answer='7', correct=True)
        from django.utils import timezone
        archived = Task.objects.last()
        archived.archived_at = timezone.now()
        archived.save()
        media_before = sorted(path.relative_to(self.root).as_posix() for path in (self.root / 'media').rglob('*') if path.is_file())
        self.assertIn('created 0, skipped 135', self.run_import(publish=True))
        first.refresh_from_db()
        archived.refresh_from_db()
        self.assertEqual((first.title, first.answer), ('Моя правка', '7'))
        self.assertIsNotNone(archived.archived_at)
        self.assertEqual(Practice.objects.get().task_id, first.pk)
        self.assertEqual(Task.all_objects.count(), 135)
        self.assertEqual(media_before, sorted(path.relative_to(self.root).as_posix() for path in (self.root / 'media').rglob('*') if path.is_file()))

    def test_bad_checksum_is_rejected_before_any_database_write(self):
        self.assets['screenshots/condition.png'] = b'corrupt image'
        self.write_bundle()
        with self.assertRaises(CommandError):
            self.run_import(publish=True)
        self.assertFalse(Task.objects.exists())
        self.assertFalse(Topic.objects.exists())

    def test_path_traversal_is_rejected(self):
        self.manifest['tasks'][0]['images'][0]['path'] = '../escape.png'
        self.write_bundle()
        with self.assertRaisesMessage(CommandError, 'Unsafe asset path'):
            self.run_import()
        self.assertFalse(Topic.objects.exists())

    def test_incomplete_or_duplicate_pilot_is_rejected(self):
        self.manifest['tasks'][-1] = dict(self.manifest['tasks'][0])
        self.write_bundle()
        with self.assertRaisesMessage(CommandError, 'Duplicate source ID'):
            self.run_import()
        self.assertFalse(Task.objects.exists())

    def test_storage_and_database_roll_back_together_on_block_error(self):
        with patch.object(TaskBlock, 'full_clean', side_effect=ValidationError('fixture failure')):
            with self.assertRaisesMessage(CommandError, 'rolled back'):
                self.run_import()
        self.assertFalse(Task.objects.exists())
        self.assertFalse(Topic.objects.exists())
        self.assertFalse(any(path.is_file() for path in (self.root / 'media').rglob('*')))

    def test_custom_topic_names_and_manual_tasks_are_preserved(self):
        topic = Topic.objects.create(number=1, title='Моя тема')
        original = Task.objects.create(topic=topic, title='Авторская задача', answer='8')
        self.run_import(sync_empty_topic_titles=True)
        topic.refresh_from_db()
        original.refresh_from_db()
        self.assertEqual(topic.title, 'Моя тема')
        self.assertEqual(original.answer, '8')
        self.assertEqual(Task.objects.count(), 136)

    def test_source_identity_is_unique_but_original_can_be_copied(self):
        self.run_import()
        task = Task.objects.first()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Task.objects.create(topic=task.topic, title='Duplicate', answer='1', source_system='kompege', external_id=task.external_id)
        self.client.force_login(self.teacher)
        response = self.client.post(reverse('duplicate_task', args=[task.pk]))
        self.assertEqual(response.status_code, 302)
        copied = Task.objects.get(title=task.title + ' · копия')
        self.assertIsNone(copied.external_id)
        self.assertEqual(copied.source_url, task.source_url)
        page = self.client.get(reverse('task', args=[copied.pk]))
        self.assertContains(page, task.source_url)
        self.assertNotContains(page, '№None')

    def test_external_id_search_and_variant_snapshots_work(self):
        self.run_import(publish=True)
        task = Task.objects.get(topic__number=1, external_id=100)
        task.title = 'Переменованное задание'
        task.save()
        self.client.force_login(self.teacher)
        for route in ('catalog', 'teacher_tasks'):
            self.assertContains(self.client.get(reverse(route), {'q': '100'}), task.title)
        variant = create_variant(creator=self.teacher, title='Пробный вариант', topics=[], count=1, task_ids=str(task.pk))
        self.assertEqual(len(variant.items.get().blocks), 2)
        self.assertEqual(variant.items.get().answer, task.answer)

    def test_source_helpers_preserve_answer_values_and_reject_other_hosts(self):
        self.assertEqual(answer_text('0012<br>34\\n56'), '0012 34 56')
        self.assertEqual(safe_name('../../27_A.txt'), '27_A.txt')
        self.assertEqual(source_url('/files/one.txt'), 'https://kompege.ru/files/one.txt')
        with self.assertRaises(ValueError):
            source_url('https://other.example/one.txt')
        rows = [{'taskId': n, 'number': 1, 'key': '0', 'text': 'source', 'hide': False} for n in range(1, 8)]
        self.assertEqual([row['taskId'] for row in select_rows(rows)], [7, 6, 5, 4, 3])
