"""Import Yandex Uchebnik collections that the teacher saved by hand (used with Yandex's permission,
for the teacher's own pupils only — the site shows them after sign-in).

One folder per collection, named as on Yandex ("№ 1 — знакомство"), inside a folder or a zip:

    № 1 — знакомство/
        01.png          condition screenshot of task 1 (more pictures: 01-2.png, 01-3.png)
        01-data.xlsx    a data file of task 1 (any name starting with "01-" that is not a picture)
        ответы.txt      one line per task: "1: 15" (several numbers separated by spaces)

Each task becomes a catalogue task (site "Яндекс"), and the collection lists them in order. A repeated
import updates the same tasks, so pupils' answers stay.
"""
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from PIL import Image
from .collection_models import Collection, CollectionItem
from .collections_builder import YANDEX_LIST, YANDEX_URL, parse_yandex
from .models import Task, TaskBlock, Topic
from .topic_defaults import TOPICS

IMAGES = {'.png', '.jpg', '.jpeg', '.webp'}
FILES = {'.txt', '.csv', '.xlsx', '.xls', '.pdf', '.zip', '.docx', '.doc', '.ods', '.odt', '.py'}
ANSWERS = ('ответы.txt', 'answers.txt')
ANSWER_LINE = re.compile(r'^\s*(?:задание|задача|№)?\s*(\d{1,2})\s*(?:(?:[).:—–]|-(?!\d))\s*|\s+)(.+?)\s*$', re.I)
TASK_FILE = re.compile(r'^(\d{1,2})(?:-(.+))?$')


def norm(title):
    """'№15 – решаем  кодом' and '№ 15 — решаем кодом' are the same collection."""
    title = re.sub(r'\s*[–—-]+\s*', ' — ', ' '.join(title.replace('ё', 'е').split()).lower())
    return re.sub(r'^№\s*(\d)', r'№ \1', title).replace(' — 21', '-21')


def difficulty(level):
    level = level.lower()
    if any(w in level for w in ('знакомство', 'проще')):
        return 'easy'
    if any(w in level for w in ('на подумать', 'кодом')):
        return 'hard'
    if any(w in level for w in ('повторение', 'уровень егэ', 'официальные')):
        return 'ege'
    return 'medium'


def read_source(path):
    """{relative posix path: bytes} from a folder or a zip."""
    path = Path(path)
    if path.is_dir():
        return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob('*') if p.is_file()}
    with zipfile.ZipFile(path) as archive:
        if sum(i.file_size for i in archive.infolist()) > 2 * 1024 ** 3:
            raise ValueError('Архив больше 2 ГБ.')
        names = {}
        for info in archive.infolist():
            if info.is_dir():
                continue
            # Windows zips keep Cyrillic names in cp866 unless flagged as UTF-8.
            name = info.filename if info.flag_bits & 0x800 else info.filename.encode('cp437').decode('cp866', 'replace')
            names[name] = archive.read(info)
        return names


def parse_answers(text, where):
    answers = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = ANSWER_LINE.match(line)
        if not match:
            raise ValueError(f'{where}: не понял строку ответа «{line.strip()}» (нужно «1: 15»).')
        answers[int(match.group(1))] = ' '.join(match.group(2).split())
    return answers


def plan(files):
    """Validate everything first: [(order, title, uuid, [{'position', 'answer', 'images', 'files'}])]."""
    known = {norm(title): (order, ' '.join(title.split()), ident) for order, (title, ident) in enumerate(json.loads(YANDEX_LIST.read_text(encoding='utf-8')), 1)}
    folders = {}
    for name, data in files.items():
        parts = PurePosixPath(name).parts
        if any(p.startswith(('.', '__MACOSX')) for p in parts) or PurePosixPath(name).name.lower() in ('thumbs.db', 'desktop.ini'):
            continue
        folder = next((p for p in parts[:-1] if p.strip().startswith('№')), None)
        if folder is None:
            raise ValueError(f'«{name}» лежит не в папке подборки (папка должна называться как на Яндексе: «№ 1 — знакомство»).')
        folders.setdefault(folder, {})[PurePosixPath(name).name] = data
    result, errors = [], []
    for folder, items in sorted(folders.items()):
        if norm(folder) not in known:
            errors.append(f'Папка «{folder}»: нет такой подборки в списке Яндекса.')
            continue
        order, title, ident = known[norm(folder)]
        answer_file = next((items[n] for n in items if n.lower() in ANSWERS), None)
        if answer_file is None:
            errors.append(f'«{folder}»: нет файла ответы.txt.')
            continue
        try:
            answers = parse_answers(answer_file.decode('utf-8-sig', 'replace'), folder)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        tasks = {}
        for name, data in items.items():
            if name.lower() in ANSWERS:
                continue
            stem, suffix = PurePosixPath(name).stem, PurePosixPath(name).suffix.lower()
            match = TASK_FILE.match(stem)
            if not match or suffix not in IMAGES | FILES:
                errors.append(f'«{folder}/{name}»: имя должно начинаться с номера задачи («01.png», «01-2.png», «01-data.xlsx»).')
                continue
            task = tasks.setdefault(int(match.group(1)), {'images': [], 'files': []})
            if suffix in IMAGES:
                try:
                    Image.open(io.BytesIO(data)).verify()
                except Exception:
                    errors.append(f'«{folder}/{name}»: картинка не открывается.')
                    continue
                task['images'].append((match.group(2) or '', name, data))
            else:
                task['files'].append((name, data))
        rows = []
        for position in sorted(set(tasks) | set(answers)):
            task = tasks.get(position, {'images': [], 'files': []})
            if not task['images']:
                errors.append(f'«{folder}», задача {position}: нет картинки с условием.')
            if position not in answers:
                errors.append(f'«{folder}», задача {position}: нет ответа в ответы.txt.')
            if not 1 <= position <= 99:
                errors.append(f'«{folder}», задача {position}: номер должен быть от 1 до 99.')
            # "01.png" first, then "01-2.png", "01-3.png"…
            images = [(name, data) for _, name, data in sorted(task['images'], key=lambda i: (i[0] != '', i[0].zfill(4)))]
            rows.append({'position': position, 'answer': answers.get(position, ''), 'images': images, 'files': sorted(task['files'])})
        result.append((order, title, ident, rows))
    if errors:
        raise ValueError('\n'.join(errors))
    if not result:
        raise ValueError('Не найдено ни одной папки подборки.')
    return result


def store(ident, name, data, written):
    digest = hashlib.sha256(data).hexdigest()
    suffix = PurePosixPath(name).suffix.lower()
    path = f'imports/yandex/{ident}/{digest[:12]}{suffix}'
    if not default_storage.exists(path):
        path = default_storage.save(path, ContentFile(data))
        written.append(path)
    return path


def import_collections(path, publish=True):
    """Returns (collections, created, updated)."""
    collections = plan(read_source(path))
    written, created, updated = [], 0, 0
    try:
        with transaction.atomic():
            for order, title, uuid, rows in collections:
                number, level = parse_yandex(title)
                topic, _ = Topic.objects.get_or_create(number=number, defaults={'title': TOPICS[number - 1]})
                collection, _ = Collection.objects.update_or_create(key=f'yandex:{uuid}', defaults={
                    'source': 'yandex', 'number': number, 'title': title, 'level': level, 'url': YANDEX_URL.format(uuid),
                    'sort_order': order, 'description': 'Подборка Яндекс Учебника', 'published': True})
                task_ids = []
                for row in rows:
                    ident = order * 100 + row['position']
                    task = Task.all_objects.filter(source_system='yandex', external_id=ident, topic=topic).first()
                    fields = {'title': f'{title} · задача {row["position"]}', 'answer': row['answer'], 'difficulty': difficulty(level),
                              'source': 'Яндекс Учебник', 'source_url': YANDEX_URL.format(uuid).replace('/task/1?start=1', f'/task/{row["position"]}'),
                              'source_label': f'Яндекс Учебник · {title}'[:120], 'tags': 'Яндекс Учебник, подборка',
                              'published': publish, 'in_catalog': True, 'archived_at': None}
                    if task is None:
                        task = Task(topic=topic, source_system='yandex', external_id=ident, **fields)
                        created += 1
                    else:
                        for key, value in fields.items():
                            setattr(task, key, value)
                        TaskBlock.all_objects.filter(task=task).delete()
                        updated += 1
                    task.full_clean()
                    task.save()
                    blocks = [('image', name, data) for name, data in row['images']] + [('file', name, data) for name, data in row['files']]
                    for position, (kind, name, data) in enumerate(blocks, 1):
                        stored = store(ident, name, data, written)
                        block = TaskBlock(task=task, kind=kind, position=position,
                                          text=f'Условие: {title}, задача {row["position"]}' if kind == 'image' else PurePosixPath(name).name,
                                          **{kind: stored})
                        block.full_clean()
                        block.save()
                    task_ids.append(task.pk)
                collection.items.all().delete()
                CollectionItem.objects.bulk_create([CollectionItem(collection=collection, task_id=pk, position=i) for i, pk in enumerate(task_ids, 1)])
    except Exception:
        for name in written:
            default_storage.delete(name)
        raise
    return len(collections), created, updated
