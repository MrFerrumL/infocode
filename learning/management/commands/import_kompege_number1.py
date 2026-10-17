"""Add a complete licensed number-1 snapshot without replacing existing tasks."""
import hashlib
import io
import json
from pathlib import PurePosixPath
import re
import zipfile
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from PIL import Image
from learning.models import Task, TaskBlock, Topic
from .import_kompege_pilot import checked_member, MAX_TOTAL

FORMAT = 'infocode-kompege-number1-v1'


def inspect_bundle(archive):
    members = archive.infolist()
    if len(members) != len({i.filename for i in members}) or len(members) > 2500 or sum(i.file_size for i in members) > MAX_TOTAL:
        raise ValueError('Duplicate, excessive or oversized archive members')
    if archive.getinfo('manifest.json').file_size > 4 * 1024 * 1024:
        raise ValueError('Oversized manifest')
    manifest = json.loads(archive.read('manifest.json'))
    rows = manifest.get('tasks')
    if manifest.get('format') != FORMAT or manifest.get('source') != 'https://kompege.ru' or not isinstance(rows, list) or not 1 <= len(rows) <= 2000:
        raise ValueError('Expected the complete number-1 bundle')
    if manifest.get('source_count') != len(rows) or not re.fullmatch('[a-f0-9]{64}', str(manifest.get('source_sha256', ''))):
        raise ValueError('Source snapshot count or checksum is missing')
    seen, assets = set(), {}
    for row in rows:
        ident = row.get('source_id')
        if type(row.get('number')) is not int or row['number'] != 1 or type(ident) is not int or not 1 <= ident <= 2_147_483_647 or ident in seen:
            raise ValueError('Only unique number-1 source IDs are allowed')
        seen.add(ident)
        if row.get('source_url') != f'https://kompege.ru/task?id={ident}' or row.get('difficulty') not in dict(Task.LEVELS):
            raise ValueError('Invalid source URL or difficulty')
        if not isinstance(row.get('answer'), str) or not row['answer'].strip() or len(row['answer']) > 250:
            raise ValueError('Every task needs a valid answer')
        if not isinstance(row.get('author'), str) or len(row['author']) > 200:
            raise ValueError('Invalid author')
        if not isinstance(row.get('images'), list) or len(row['images']) != 1 or not isinstance(row.get('files'), list) or len(row['files']) > 20:
            raise ValueError('A complete condition image is required')
        for image, items in ((True, row['images']), (False, row['files'])):
            for item in items:
                data = checked_member(archive, item, image)
                if image:
                    with Image.open(io.BytesIO(data)) as picture:
                        if picture.width != 1920:
                            raise ValueError('Condition must be a native 1920px screenshot')
                assets[item['path']] = data
    if {i.filename for i in members} != set(assets) | {'manifest.json'}:
        raise ValueError('Unexpected archive members')
    return manifest, assets


class Command(BaseCommand):
    help = 'Import all public licensed KOMPEGE number-1 conditions; preserve all existing tasks and attempts.'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--publish', action='store_true')

    def handle(self, path, dry_run=False, publish=False, **options):
        try:
            with zipfile.ZipFile(path) as archive:
                manifest, assets = inspect_bundle(archive)
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, Image.DecompressionBombError) as exc:
            raise CommandError('Number-1 validation failed: ' + str(exc)) from exc
        rows = manifest['tasks']
        existing = set(Task.all_objects.filter(source_system='kompege', topic__number=1).values_list('external_id', flat=True))
        self.stdout.write(f'Validated {len(rows)} number-1 tasks; new {sum(r["source_id"] not in existing for r in rows)}.')
        if dry_run:
            self.stdout.write('Dry run: no database or media changes.')
            return
        written, created, skipped = [], 0, 0
        try:
            with transaction.atomic():
                topic, _ = Topic.objects.get_or_create(number=1, defaults={'title': 'Анализ информационных моделей'})
                topic = Topic.objects.select_for_update().get(pk=topic.pk)
                for row in rows:
                    ident = row['source_id']
                    if Task.all_objects.filter(topic=topic, source_system='kompege', external_id=ident).exists():
                        skipped += 1
                        continue
                    task = Task(topic=topic, title=f'КОМПЕГЭ №{ident} · задание 1', answer=row['answer'],
                        difficulty=row['difficulty'], source='КОМПЕГЭ' + (f' · {row["author"]}' if row['author'] else ''),
                        source_system='kompege', external_id=ident, source_url=row['source_url'], published=publish,
                        tags='КОМПЕГЭ, полный банк №1')
                    task.full_clean()
                    task.save()
                    for position, (item, image) in enumerate([(i, True) for i in row['images']] + [(i, False) for i in row['files']], 1):
                        basename = PurePosixPath(item['path']).name if image else item['name']
                        name = f'imports/kompege/{ident}/{item["sha256"][:12]}/{basename}'
                        if len(name) > 100:
                            raise ValueError('Storage filename too long')
                        if default_storage.exists(name):
                            with default_storage.open(name, 'rb') as handle:
                                if hashlib.sha256(handle.read()).hexdigest() != item['sha256']:
                                    raise ValueError('Existing media checksum mismatch')
                        else:
                            actual = default_storage.save(name, ContentFile(assets[item['path']]))
                            written.append(actual)
                            name = actual
                        block = TaskBlock(task=task, kind='image' if image else 'file', position=position,
                            text=f'Условие КОМПЕГЭ №{ident}' if image else item['name'],
                            **({'image': name} if image else {'file': name}))
                        block.full_clean()
                        block.save()
                    created += 1
        except Exception:
            for name in written:
                default_storage.delete(name)
            raise
        self.stdout.write(self.style.SUCCESS(f'Number-1 imported: created {created}, skipped {skipped}. Existing data preserved.'))
