"""Import a verified, image-based 135-card licensed pilot without overwriting tasks."""
from collections import Counter
import hashlib
import io
import json
from pathlib import PurePosixPath
import zipfile

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from PIL import Image

from learning.models import Task, TaskBlock, Topic
from learning.topic_defaults import TOPICS


FORMAT = 'infocode-kompege-pilot-v1'
MAX_FILE = 50 * 1024 * 1024  # largest number-17 data files are about 31 MB
MAX_TOTAL = 256 * 1024 * 1024


def checked_member(archive, item, image=False):
    if not isinstance(item, dict) or not isinstance(item.get('path'), str):
        raise ValueError('Invalid file descriptor')
    path = PurePosixPath(item['path'])
    if path.is_absolute() or '..' in path.parts or '\\' in item['path'] or not path.parts or path.parts[0] not in ('screenshots', 'attachments'):
        raise ValueError('Unsafe asset path')
    info = archive.getinfo(str(path))
    if info.is_dir() or info.file_size > MAX_FILE or (info.external_attr >> 16) & 0o170000 == 0o120000:
        raise ValueError('Invalid or oversized asset')
    data = archive.read(info)
    if hashlib.sha256(data).hexdigest() != item.get('sha256'):
        raise ValueError('Asset checksum mismatch: ' + str(path))
    if image:
        if path.suffix.lower() not in ('.png', '.jpg', '.jpeg'):
            raise ValueError('Condition screenshots must be PNG or JPEG')
        with Image.open(io.BytesIO(data)) as picture:
            if picture.format not in ('PNG', 'JPEG') or picture.width < 20 or picture.height < 15 or picture.width * picture.height > 40_000_000:
                raise ValueError('Invalid screenshot dimensions')
            picture.verify()
    else:
        name = item.get('name', '')
        if not isinstance(name, str) or not name or name != PurePosixPath(name).name or '\\' in name or len(name) > 90:
            raise ValueError('Invalid original filename')
        allowed = {'txt', 'csv', 'xlsx', 'xls', 'pdf', 'zip', 'docx', 'doc', 'ods', 'odt', 'py'}
        if PurePosixPath(name).suffix.lower().lstrip('.') not in allowed:
            raise ValueError('Unsupported attachment: ' + name)
    return data


def inspect_bundle(archive):
    members = archive.infolist()
    if len(members) != len({item.filename for item in members}) or len(members) > 1500:
        raise ValueError('Duplicate or excessive archive members')
    if sum(item.file_size for item in members) > MAX_TOTAL or archive.getinfo('manifest.json').file_size > 2 * 1024 * 1024:
        raise ValueError('Pilot archive is too large')
    manifest = json.loads(archive.read('manifest.json'))
    rows = manifest.get('tasks')
    if manifest.get('format') != FORMAT or manifest.get('source') != 'https://kompege.ru' or not isinstance(rows, list) or len(rows) != 135:
        raise ValueError('Expected a 135-card KOMPEGE pilot')
    seen, assets = set(), {}
    for row in rows:
        number, external_id = row.get('number'), row.get('source_id')
        if type(number) is not int or not 1 <= number <= 27 or type(external_id) is not int or not 1 <= external_id <= 2_147_483_647:
            raise ValueError('Invalid EGE number or source ID')
        key = (number, external_id)
        if key in seen:
            raise ValueError('Duplicate source ID within a number')
        seen.add(key)
        if row.get('source_url') != f'https://kompege.ru/task?id={external_id}':
            raise ValueError('Invalid source reference')
        if not isinstance(row.get('answer'), str) or not row['answer'].strip() or len(row['answer']) > 250:
            raise ValueError('A valid answer is required for every card')
        if row.get('difficulty') not in dict(Task.LEVELS) or not isinstance(row.get('author'), str) or len(row['author']) > 200:
            raise ValueError('Invalid task metadata')
        if not isinstance(row.get('images'), list) or not 1 <= len(row['images']) <= 5 or not isinstance(row.get('files'), list) or len(row['files']) > 20:
            raise ValueError('Missing screenshots or excessive attachments')
        for image, items in ((True, row['images']), (False, row['files'])):
            for item in items:
                data = checked_member(archive, item, image)
                previous = assets.get(item['path'])
                if previous and previous != data:
                    raise ValueError('Conflicting repeated asset')
                assets[item['path']] = data
    if Counter(row['number'] for row in rows) != Counter({number: 5 for number in range(1, 28)}):
        raise ValueError('Every EGE number must have exactly five tasks')
    if {item.filename for item in members} != set(assets) | {'manifest.json'}:
        raise ValueError('Unexpected archive members')
    titles = manifest.get('topic_titles', {})
    if not isinstance(titles, dict) or any(not isinstance(title, str) or not title.strip() or len(title) > 150 for title in titles.values()):
        raise ValueError('Invalid topic titles')
    return manifest, assets


class Command(BaseCommand):
    help = 'Validate/import exactly 5 licensed screenshot-based KOMPEGE tasks for each of 27 numbers.'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--publish', action='store_true')
        parser.add_argument('--sync-empty-topic-titles', action='store_true', help='Only rename empty, untouched default topics to the source labels.')

    def handle(self, path, dry_run=False, publish=False, sync_empty_topic_titles=False, **options):
        try:
            with zipfile.ZipFile(path) as archive:
                manifest, assets = inspect_bundle(archive)
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, Image.DecompressionBombError) as exc:
            raise CommandError('Pilot validation failed: ' + str(exc)) from exc
        rows = manifest['tasks']
        existing = set(Task.all_objects.filter(source_system='kompege').values_list('topic__number', 'external_id'))
        new_rows = [row for row in rows if (row['number'], row['source_id']) not in existing]
        self.stdout.write(f'Validated: 135 cards, {len(assets)} media files; new {len(new_rows)}, already present {135-len(new_rows)}.')
        if dry_run:
            self.stdout.write('Dry run: no database or media changes.')
            return
        written, created, skipped = [], 0, 0

        def save_asset(item, external_id, image):
            data = assets[item['path']]
            basename = PurePosixPath(item['path']).name if image else item['name']
            # Keep original attachment names for downloads and content hashes for safe reuse.
            name = f'imports/kompege/{external_id}/{item["sha256"][:12]}/{basename}'
            if len(name) > 100:
                raise ValueError('Storage filename is too long')
            if default_storage.exists(name):
                with default_storage.open(name, 'rb') as handle:
                    if hashlib.sha256(handle.read()).hexdigest() != item['sha256']:
                        raise ValueError('Existing media has a different checksum')
                return name
            actual = default_storage.save(name, ContentFile(data))
            written.append(actual)
            return actual

        try:
            with transaction.atomic():
                topics = {}
                for number in range(1, 28):
                    topic, _ = Topic.objects.get_or_create(number=number, defaults={'title': TOPICS[number-1]})
                    topic = Topic.objects.select_for_update().get(pk=topic.pk)
                    title = manifest.get('topic_titles', {}).get(str(number))
                    if sync_empty_topic_titles and title and topic.title == TOPICS[number-1] and not Task.all_objects.filter(topic=topic).exists():
                        topic.title = title
                        topic.save(update_fields=['title'])
                    topics[number] = topic
                for row in rows:
                    external_id = row['source_id']
                    if Task.all_objects.filter(source_system='kompege', external_id=external_id, topic=topics[row['number']]).exists():
                        skipped += 1
                        continue
                    source = 'КОМПЕГЭ' + (f' · {row["author"]}' if row['author'] else '')
                    task = Task(topic=topics[row['number']], title=f'КОМПЕГЭ №{external_id} · задание {row["number"]}',
                                answer=row['answer'], difficulty=row['difficulty'], source=source,
                                source_system='kompege', external_id=external_id, source_url=row['source_url'],
                                tags='КОМПЕГЭ, пробная подборка', published=publish)
                    task.full_clean()
                    task.save()
                    position = 0
                    for item in row['images']:
                        position += 1
                        block = TaskBlock(task=task, kind='image', position=position,
                                          text=f'Условие КОМПЕГЭ №{external_id}' if position == 1 else f'Вопрос задания {row["number"]}',
                                          image=save_asset(item, external_id, True))
                        block.full_clean()
                        block.save()
                    for item in row['files']:
                        position += 1
                        block = TaskBlock(task=task, kind='file', position=position, text=item['name'], file=save_asset(item, external_id, False))
                        block.full_clean()
                        block.save()
                    created += 1
        except Exception as exc:
            for name in written:
                default_storage.delete(name)
            if isinstance(exc, (ValueError, ValidationError, OSError)):
                raise CommandError('Import rolled back: ' + str(exc)) from exc
            raise
        self.stdout.write(self.style.SUCCESS(f'Pilot imported: created {created}, skipped {skipped}. Existing tasks and pupil history were preserved.'))
