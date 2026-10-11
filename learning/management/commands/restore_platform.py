import json
import tempfile
import zipfile
from pathlib import Path
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from learning.models import Task, Course


class Command(BaseCommand):
    help = 'Restore backup into an EMPTY migrated database and empty media directory (SQLite or PostgreSQL).'

    def add_arguments(self, parser):
        parser.add_argument('path')

    def handle(self, path, **options):
        if get_user_model().objects.exists() or Task.objects.exists() or Course.objects.exists():
            raise CommandError('Restore is only allowed into an empty database. Existing data is not overwritten.')
        media = Path(settings.MEDIA_ROOT).resolve()
        if media.exists() and any(media.iterdir()):
            raise CommandError('MEDIA_ROOT must be empty.')
        call_command('backup_platform', path, verify=True)
        with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as temp:
            for name in archive.namelist():
                if name.startswith('media/'):
                    destination = (media / name[6:]).resolve()
                    if not destination.is_relative_to(media):
                        raise CommandError('Unsafe backup path')
            data_file = Path(temp) / 'data.json'
            data_file.write_bytes(archive.read('data.json'))
            with transaction.atomic():
                call_command('loaddata', str(data_file), verbosity=0)
                for name in archive.namelist():
                    if name.startswith('media/') and not name.endswith('/'):
                        destination = media / name[6:]
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(archive.read(name))
        self.stdout.write('Restored data and media. Database: ' + settings.DATABASES['default']['ENGINE'])
