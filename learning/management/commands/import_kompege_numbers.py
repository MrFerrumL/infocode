"""Add a complete licensed category snapshots without replacing existing tasks."""
from collections import Counter
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
from learning.models import Bookmark, Practice, Task, TaskBlock, Topic, VariantItem
from .import_kompege_pilot import checked_member
from learning.topic_defaults import TOPICS

# Number 24 alone carries ~1.1 GB of TXT data; data files are streamed, never held together.
MAX_TOTAL = 3 * 1024 * 1024 * 1024

FORMAT = 'infocode-kompege-numbers-v1'
# Numbers 19–21 share one condition: 19 has it alone, 20 and 21 add their own question image.
GAMES_FORMAT = 'infocode-kompege-games-v1'
GAMES = [19, 20, 21]


def inspect_bundle(archive, numbers=None):
    members = archive.infolist()
    if len(members) != len({i.filename for i in members}) or len(members) > 2500 or sum(i.file_size for i in members) > MAX_TOTAL:
        raise ValueError('Duplicate, excessive or oversized archive members')
    if archive.getinfo('manifest.json').file_size > 4 * 1024 * 1024:
        raise ValueError('Oversized manifest')
    manifest = json.loads(archive.read('manifest.json'))
    rows = manifest.get('tasks')
    games = manifest.get('format') == GAMES_FORMAT
    if manifest.get('format') not in (FORMAT, GAMES_FORMAT) or manifest.get('source') != 'https://kompege.ru' or not isinstance(rows, list) or not 1 <= len(rows) <= 2000:
        raise ValueError('Expected complete category snapshots')
    selected = manifest.get('numbers')
    if not isinstance(selected, list) or not selected or any(type(n) is not int or not 1 <= n <= 27 for n in selected) or selected != sorted(set(selected)):
        raise ValueError('Invalid category selection')
    if games and selected != GAMES:
        raise ValueError('Game groups must contain numbers 19, 20 and 21')
    if numbers is not None and selected != sorted(set(numbers)):
        raise ValueError('Bundle categories do not match requested categories')
    counts, hashes = manifest.get('source_counts'), manifest.get('source_sha256')
    if not isinstance(counts, dict) or not isinstance(hashes, dict) or set(counts) != {str(n) for n in selected} or set(hashes) != set(counts):
        raise ValueError('Missing source snapshot metadata')
    if any(type(counts[str(n)]) is not int or counts[str(n)] < 1 or not re.fullmatch('[a-f0-9]{64}', str(hashes[str(n)])) for n in selected):
        raise ValueError('Invalid source snapshot count or checksum')
    seen, assets = set(), {}
    for row in rows:
        ident = row.get('source_id')
        if type(row.get('number')) is not int or row['number'] not in selected or type(ident) is not int or not 1 <= ident <= 2_147_483_647 or (row['number'], ident) in seen:
            raise ValueError('Only unique source IDs in selected categories are allowed')
        seen.add((row['number'], ident))
        if row.get('source_url') != f'https://kompege.ru/task?id={ident}' or row.get('difficulty') not in dict(Task.LEVELS):
            raise ValueError('Invalid source URL or difficulty')
        if not isinstance(row.get('answer'), str) or not row['answer'].strip() or len(row['answer']) > 250:
            raise ValueError('Every task needs a valid answer')
        if not isinstance(row.get('author'), str) or len(row['author']) > 200:
            raise ValueError('Invalid author')
        if not isinstance(row.get('images'), list) or len(row['images']) != (2 if games and row['number'] != 19 else 1) or not isinstance(row.get('files'), list) or len(row['files']) > 20:
            raise ValueError('A complete condition image is required')
        for image, items in ((True, row['images']), (False, row['files'])):
            for item in items:
                data = checked_member(archive, item, image)
                if image:
                    with Image.open(io.BytesIO(data)) as picture:
                        if picture.width != 1920:
                            raise ValueError('Condition must be a native 1920px screenshot')
                # Data files are verified here and read again one by one when saved.
                assets[item['path']] = data if image else None
    if games:
        groups = {}
        for row in rows:
            groups.setdefault(row['source_id'], {})[row['number']] = row
        for ident, group in groups.items():
            if sorted(group) != GAMES or any(group[n]['images'][0] != group[19]['images'][0] for n in (20, 21)):
                raise ValueError(f'Game group {ident} must share one condition across 19, 20 and 21')
    if Counter(row['number'] for row in rows) != Counter({int(n): count for n, count in counts.items()}):
        raise ValueError('Category snapshot is incomplete')
    if {i.filename for i in members} != set(assets) | {'manifest.json'}:
        raise ValueError('Unexpected archive members')
    return manifest, assets


def remove_unreferenced(names):
    used = set(TaskBlock.all_objects.filter(image__in=names).values_list('image', flat=True))
    used |= set(TaskBlock.all_objects.filter(file__in=names).values_list('file', flat=True))
    for name in names - used:
        if default_storage.exists(name):
            default_storage.delete(name)


class Command(BaseCommand):
    help = 'Import all public licensed KOMPEGE categories; preserve all existing tasks and attempts.'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--numbers', nargs='+', type=int, required=True)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--publish', action='store_true')
        parser.add_argument('--replace-existing', action='store_true',
            help='Delete earlier KOMPEGE tasks of these numbers first; refused if pupils already used them.')

    def handle(self, path, numbers, dry_run=False, publish=False, replace_existing=False, **options):
        try:
            with zipfile.ZipFile(path) as archive:
                manifest, assets = inspect_bundle(archive, numbers)
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, Image.DecompressionBombError) as exc:
            raise CommandError('Category validation failed: ' + str(exc)) from exc
        rows = manifest['tasks']
        old = Task.all_objects.filter(source_system='kompege', topic__number__in=numbers)
        if replace_existing:
            used = Practice.objects.filter(task__in=old).count() + Bookmark.objects.filter(task__in=old).count() + VariantItem.objects.filter(task__in=old).count()
            if used:
                raise CommandError(f'Refusing to replace: earlier tasks have {used} pupil answers, bookmarks or variant items.')
            self.stdout.write(f'Replacing {old.count()} earlier KOMPEGE tasks in numbers {", ".join(map(str, sorted(set(numbers))))}.')
            existing = set()
        else:
            existing = set(old.values_list('topic__number', 'external_id'))
        self.stdout.write(f'Validated {len(rows)} category tasks; new {sum((r["number"], r["source_id"]) not in existing for r in rows)}.')
        if dry_run:
            self.stdout.write('Dry run: no database or media changes.')
            return
        written, created, skipped = [], 0, 0
        try:
            with transaction.atomic():
                topics = {}
                for number in sorted(set(numbers)):
                    topic, _ = Topic.objects.get_or_create(number=number, defaults={'title': TOPICS[number-1]})
                    topics[number] = Topic.objects.select_for_update().get(pk=topic.pk)
                if replace_existing:
                    stale = set(TaskBlock.all_objects.filter(task__in=old).exclude(image='').values_list('image', flat=True))
                    stale |= set(TaskBlock.all_objects.filter(task__in=old).exclude(file='').values_list('file', flat=True))
                    old.delete()
                    # Files go only after the new tasks are committed (a failed import keeps everything),
                    # and only if no block, including a new one reusing the same file, still refers to them.
                    transaction.on_commit(lambda: remove_unreferenced(stale))
                for row in rows:
                    ident = row['source_id']
                    topic = topics[row['number']]
                    if Task.all_objects.filter(topic=topic, source_system='kompege', external_id=ident).exists():
                        skipped += 1
                        continue
                    task = Task(topic=topic, title=f'КОМПЕГЭ №{ident} · задание {row["number"]}', answer=row['answer'],
                        difficulty=row['difficulty'], source='КОМПЕГЭ' + (f' · {row["author"]}' if row['author'] else ''),
                        source_system='kompege', external_id=ident, source_url=row['source_url'], published=publish,
                        tags=f'КОМПЕГЭ, полный банк №{row["number"]}')
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
                            data = assets[item['path']]
                            if data is None:
                                with zipfile.ZipFile(path) as archive:
                                    data = checked_member(archive, item)
                            actual = default_storage.save(name, ContentFile(data))
                            written.append(actual)
                            name = actual
                        block = TaskBlock(task=task, kind='image' if image else 'file', position=position,
                            text=(f'Условие КОМПЕГЭ №{ident}' if position == 1 else f'Вопрос задания {row["number"]}') if image else item['name'],
                            **({'image': name} if image else {'file': name}))
                        block.full_clean()
                        block.save()
                    created += 1
        except Exception:
            for name in written:
                default_storage.delete(name)
            raise
        self.stdout.write(self.style.SUCCESS(f'Categories imported: created {created}, skipped {skipped}. Existing data preserved.'))
