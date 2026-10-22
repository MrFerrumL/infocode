"""Import tasks of the FIPI open bank (informatics) that are new for the platform.

The bundle (manifest.json, format infocode-fipi-bank-v2) is made from the bank pages: each task has its FIPI
number and GUID, the exam number it belongs to (matched to the nearest KOMPEGE task by the text model) and
its condition as blocks — formatted text with tables, pictures and data files in the original order.
FIPI does not publish answers: they are checked by FIPI on the first attempts and then remembered
(learning/fipi_check.py). Re-importing keeps answers already learned.
"""
import hashlib
import io
import json
import re
import zipfile
from pathlib import PurePosixPath
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from PIL import Image
from learning.models import Task, TaskBlock, Topic
from learning.rich_text import sanitize_html
from learning.subjects import using
from learning.topic_defaults import TOPICS

FORMATS = ('infocode-fipi-bank-v1', 'infocode-fipi-bank-v2')
FILE_TYPES = {'.txt', '.csv', '.xlsx', '.xls', '.pdf', '.zip', '.docx', '.doc', '.ods', '.odt', '.py'}


def blocks_of(row):
    if 'blocks' in row:
        return row['blocks']
    return [{'kind': 'html', 'text': row.get('html', '')}]


class Command(BaseCommand):
    help = 'Import new FIPI open-bank tasks (answers are checked by FIPI and remembered).'

    def add_arguments(self, parser):
        parser.add_argument('path')

    def handle(self, path, **options):
        try:
            archive = zipfile.ZipFile(path)
            manifest = json.loads(archive.read('manifest.json'))
        except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
            raise CommandError(f'Not a FIPI bundle: {exc}') from exc
        rows = manifest.get('tasks')
        if manifest.get('format') not in FORMATS or not isinstance(rows, list) or not 1 <= len(rows) <= 5000:
            raise CommandError('Expected a FIPI bank bundle.')
        names = set(archive.namelist())
        for row in rows:
            ok = (isinstance(row.get('number'), int) and 1 <= row['number'] <= 27 and re.fullmatch(r'[0-9A-Fa-f]{1,32}', str(row.get('guid', '')))
                  and isinstance(row.get('external_id'), int) and blocks_of(row))
            for block in blocks_of(row) if ok else []:
                kind = block.get('kind')
                if kind == 'html':
                    ok = ok and isinstance(block.get('text'), str) and bool(block['text'].strip())
                elif kind in ('image', 'file'):
                    member = PurePosixPath(str(block.get('path', '')))
                    ok = ok and str(member) in names and '..' not in member.parts and member.parts[0] in ('images', 'files')
                    if kind == 'file':
                        ok = ok and member.suffix.lower() in FILE_TYPES
                    else:
                        ok = ok and member.suffix.lower() in ('.png', '.jpg', '.jpeg')
                else:
                    ok = False
            if not ok:
                raise CommandError(f'Invalid task {row.get("id")!r}')
        project = manifest.get('project', '')
        created = updated = 0
        written = []
        try:
            with using('inf'), transaction.atomic():
                topics = {}
                for number in sorted({r['number'] for r in rows}):
                    topics[number], _ = Topic.objects.get_or_create(number=number, defaults={'title': TOPICS[number - 1]})
                for row in rows:
                    task = Task.all_objects.filter(source_system='fipi', external_id=row['external_id']).first()
                    fields = {'topic': topics[row['number']], 'title': f'ФИПИ · задание {row["number"]} · № {row["id"]}', 'statement': '',
                              'source': 'Открытый банк заданий ЕГЭ (ФИПИ)', 'source_label': 'Открытый банк ФИПИ', 'source_kind': 'probe',
                              'source_url': f'https://ege.fipi.ru/bank/index.php?proj={project}', 'check_ref': row['guid'],
                              'difficulty': 'medium', 'tags': 'ФИПИ, открытый банк' + (', КЭС ' + ' '.join(row.get('kes', [])) if row.get('kes') else ''),
                              'published': True, 'in_catalog': True, 'archived_at': None}
                    if task is None:
                        task = Task(source_system='fipi', external_id=row['external_id'], answer='', **fields)
                        created += 1
                    else:  # keep an answer already learned from FIPI
                        for key, value in fields.items():
                            setattr(task, key, value)
                        updated += 1
                    task.save()
                    TaskBlock.all_objects.filter(task=task).delete()
                    for position, block in enumerate(blocks_of(row), 1):
                        if block['kind'] == 'html':
                            TaskBlock.objects.create(task=task, kind='html', position=position, text=sanitize_html(block['text']))
                            continue
                        data = archive.read(block['path'])
                        if block['kind'] == 'image':
                            with Image.open(io.BytesIO(data)) as picture:
                                picture.verify()
                        suffix = PurePosixPath(block['path']).suffix.lower()
                        name = f'imports/fipi/{row["id"]}/{hashlib.sha256(data).hexdigest()[:12]}{suffix}'
                        if not default_storage.exists(name):
                            name = default_storage.save(name, ContentFile(data))
                            written.append(name)
                        TaskBlock.objects.create(task=task, kind=block['kind'], position=position,
                                                 text=block.get('name', '') if block['kind'] == 'file' else 'Рисунок к заданию',
                                                 **{block['kind']: name})
        except Exception:
            for name in written:
                default_storage.delete(name)
            raise
        self.stdout.write(self.style.SUCCESS(f'FIPI: tasks created {created}, updated {updated}.'))
