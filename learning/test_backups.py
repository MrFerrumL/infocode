"""Snapshot tests need committed fixtures, not TestCase's outer transaction."""
import io
import json
import tempfile
import zipfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from .models import (Course, Lesson, Notebook, NotebookCell, ScheduleEvent,
                     ScheduleOccurrence, Section, Step, StepContentBlock, Task, Topic)
from .platform_views import archive


class PortableBackupTests(TransactionTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        setting = override_settings(MEDIA_ROOT=self.folder / 'uploads')
        setting.enable()
        self.addCleanup(setting.disable)
        self.teacher = get_user_model().objects.create_user('backup-teacher', is_staff=True)

    def backup(self):
        target = self.folder / 'backup.zip'
        call_command('backup_platform', str(target), stdout=io.StringIO())
        call_command('backup_platform', str(target), verify=True, stdout=io.StringIO())
        return target

    def test_backup_includes_archived_data_and_detects_tampering(self):
        topic = Topic.objects.create(number=1, title='Graphs')
        task = Task.objects.create(topic=topic, title='Archived task', statement='Question', answer='42')
        archive(self.teacher, task)
        target = self.backup()
        with zipfile.ZipFile(target) as source:
            rows = json.loads(source.read('data.json'))
        saved = next(r for r in rows if r['model'] == 'learning.task' and r['pk'] == task.pk)
        self.assertTrue(saved['fields']['archived_at'])
        with zipfile.ZipFile(target, 'a') as source:
            source.writestr('data.json', '[]')
        with self.assertRaises(CommandError):
            call_command('backup_platform', str(target), verify=True, stdout=io.StringIO())

    def test_portable_backup_contains_calendar_and_exception(self):
        start = timezone.now()
        event = ScheduleEvent.objects.create(teacher=self.teacher, title='Calendar', starts_at=start, ends_at=start + timedelta(hours=1))
        ScheduleOccurrence.objects.create(event=event, original_date=date(2026, 10, 26), title='Moved', starts_at=start, ends_at=start + timedelta(hours=2))
        with zipfile.ZipFile(self.backup()) as source:
            rows = json.loads(source.read('data.json'))
        self.assertTrue(any(r['model'] == 'learning.scheduleevent' and r['pk'] == event.pk for r in rows))
        self.assertTrue(any(r['model'] == 'learning.scheduleoccurrence' for r in rows))

    def test_backup_keeps_archived_notebooks_media_and_course_reference(self):
        book = Notebook.objects.create(owner=self.teacher, title='Notebook', published=True)
        # The backup preserves bytes; image decoding is covered by notebook tests.
        photo = NotebookCell.objects.create(notebook=book, kind='image', image=SimpleUploadedFile('figure.png', b'\x89PNG-test'))
        course = Course.objects.create(owner=self.teacher, title='Course')
        section = Section.objects.create(course=course, title='Section')
        lesson = Lesson.objects.create(section=section, title='Lesson')
        step = Step.objects.create(lesson=lesson, kind='notebook', title='Notebook', use_blocks=True)
        link = StepContentBlock.objects.create(step=step, kind='notebook', notebook=book)
        archive(self.teacher, book)
        with zipfile.ZipFile(self.backup()) as source:
            rows = json.loads(source.read('data.json'))
            saved = next(r for r in rows if r['model'] == 'learning.notebook' and r['pk'] == book.pk)
            cell = next(r for r in rows if r['model'] == 'learning.notebookcell' and r['pk'] == photo.pk)
            reference = next(r for r in rows if r['model'] == 'learning.stepcontentblock' and r['pk'] == link.pk)
            self.assertTrue(saved['fields']['archived_at'])
            self.assertEqual(cell['fields']['notebook'], book.pk)
            self.assertEqual(reference['fields']['notebook'], book.pk)
            self.assertEqual(source.read('media/' + photo.image.name), b'\x89PNG-test')

    def test_postgresql_snapshot_is_repeatable_read_and_read_only(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL transaction guarantees')
        from learning.management.commands import backup_platform
        original = backup_platform.call_command
        def inspect_snapshot(*args, **kwargs):
            with connection.cursor() as cursor:
                cursor.execute('SHOW transaction_isolation')
                self.assertEqual(cursor.fetchone()[0], 'repeatable read')
                cursor.execute('SHOW transaction_read_only')
                self.assertEqual(cursor.fetchone()[0], 'on')
            return original(*args, **kwargs)
        with patch.object(backup_platform, 'call_command', side_effect=inspect_snapshot):
            self.backup()

    def test_nested_postgresql_snapshot_fails_before_publication(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL transaction guarantees')
        with transaction.atomic():
            with self.assertRaisesRegex(CommandError, 'outside an existing'):
                self.backup()
        self.assertFalse((self.folder / 'backup.zip').exists())
