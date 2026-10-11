import hashlib
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone


class Command(BaseCommand):
    help = 'Create a portable data + media backup with SHA-256 manifest; --verify checks every entry.'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--verify', action='store_true')

    def handle(self, path, verify=False, **options):
        target = Path(path).resolve()
        if verify:
            self.verify_archive(target)
            self.stdout.write('Backup verified: ' + str(target))
            return
        if target.exists():
            raise CommandError('Choose a new backup filename; existing backups are never overwritten.')
        if target.is_relative_to(Path(settings.MEDIA_ROOT).resolve()):
            raise CommandError('Store the backup outside MEDIA_ROOT.')
        target.parent.mkdir(parents=True, exist_ok=True)
        data = io.StringIO()
        with transaction.atomic():
            if connection.vendor == 'postgresql':
                with connection.cursor() as cursor:
                    cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            call_command('dumpdata', use_base_manager=True, exclude=['contenttypes', 'auth.permission', 'sessions', 'admin.logentry', 'learning.loginbucket', 'learning.actionreceipt'], use_natural_foreign_keys=True, use_natural_primary_keys=True, stdout=data)
        manifest = {'created_at': timezone.now().isoformat(), 'database': connection.vendor, 'sha256': {}}
        # Publish only a complete, verified archive. An interrupted write must not
        # leave today's final filename occupied by a partial backup.
        with tempfile.TemporaryDirectory(prefix='.infocode-backup-', dir=target.parent) as folder:
            temporary = Path(folder) / 'backup.zip'
            with zipfile.ZipFile(temporary, 'x', zipfile.ZIP_DEFLATED) as archive:
                payload = data.getvalue().encode()
                archive.writestr('data.json', payload)
                manifest['sha256']['data.json'] = hashlib.sha256(payload).hexdigest()
                media = Path(settings.MEDIA_ROOT).resolve()
                for file in media.rglob('*'):
                    if file.is_file() and not file.is_symlink() and '.previews' not in file.relative_to(media).parts:
                        name = 'media/' + file.relative_to(media).as_posix()
                        digest = hashlib.sha256()
                        with file.open('rb') as source, archive.open(name, 'w', force_zip64=True) as output:
                            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                                output.write(chunk)
                                digest.update(chunk)
                        manifest['sha256'][name] = digest.hexdigest()
                archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
            self.verify_archive(temporary)
            try:
                # Same filesystem: atomic visibility, and an existing target is
                # never replaced even if another backup finished concurrently.
                os.link(temporary, target)
            except FileExistsError as exc:
                raise CommandError('Another backup already created this filename; choose a new one.') from exc
        self.stdout.write('Backup created and verified: ' + str(target))

    def verify_archive(self, target):
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            manifest = json.loads(archive.read('manifest.json'))
            checksums = manifest['sha256']
            if len(names) != len(set(names)) or set(names) != set(checksums) | {'manifest.json'} or 'data.json' not in checksums:
                raise CommandError('Invalid archive manifest or duplicate entries')
            for name, expected in checksums.items():
                digest = hashlib.sha256()
                with archive.open(name) as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b''):
                        digest.update(chunk)
                if digest.hexdigest() != expected:
                    raise CommandError('Checksum mismatch: ' + name)
