"""Prepare KOMPEGE exam variants (archive page) for import.

Input folder: list.json — [school year, collection, title, kim] in archive order — and raw/<kim>.json
from https://kompege.ru/api/v1/variant/kim/<kim> (downloaded when missing).

Tasks already imported into the catalogue (listed in the bank package manifests) are referenced by (number, source ID). Tasks the source keeps out
of its bank become hidden tasks: their conditions are rendered for native screenshots exactly like
the categories, original files are downloaded, and 19–21 groups share one condition.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
try:
    from .condition_render import Condition, HEAD, TAIL
    from .prepare_kompege_numbers import image_resolver, MAX_DATA_FILE
    from .prepare_kompege_pilot import answer_text, fetch, safe_name, source_url
except ImportError:
    from condition_render import Condition, HEAD, TAIL
    from prepare_kompege_numbers import image_resolver, MAX_DATA_FILE
    from prepare_kompege_pilot import answer_text, fetch, safe_name, source_url

FORMAT = 'infocode-kompege-variants-v1'
LEVELS = {0: 'easy', 1: 'medium', 2: 'hard', 3: 'grave'}
FILE_TYPES = ('.txt', '.csv', '.xlsx', '.xls', '.pdf', '.zip', '.docx', '.doc', '.ods', '.odt', '.py')
# Since 2025 KOMPEGE files the text-search task as type 110 and places its types 10 and 13 at
# exam positions 13 and 23; these are the exam numbers shown to pupils.
EXAM_NUMBER = {110: 10, 112: 12, 10: 13, 13: 23}


def topic_of(kind):
    return kind if kind <= 27 else kind % 100


def exam_year(school_year):
    return int(school_year.split('/')[0]) + 1


def answer_rows(key):
    rows = [answer_text(part) for part in str(key or '').replace('\r', '').replace('\\n', '\n').split('\n')]
    return '\n'.join(row for row in rows if row)


def prepare(folder, catalog_folders, refresh=False):
    folder = folder.resolve()
    for sub in ('raw', 'captures', 'source-images', 'attachments'):
        (folder / sub).mkdir(exist_ok=True)
    catalog = set()
    # Manifests of the imported bank packages list exactly what the catalogue holds.
    for base in catalog_folders:
        for row in json.loads((Path(base) / 'manifest.json').read_text(encoding='utf-8'))['tasks']:
            catalog.add((int(row['number']), int(row['source_id'])))
    if not catalog:
        raise ValueError('Bank package manifests are required')
    missing_images = []
    local_image = image_resolver(folder, lenient=True, missing=missing_images)

    def render(text, name):
        parser = Condition(image_resolver=local_image, any_https=True)
        parser.feed(text)
        parser.close()
        (folder / (name + '.html')).write_text(HEAD + ''.join(parser.parts) + TAIL, encoding='utf-8')

    tasks, jobs, variants, skipped = {}, [], [], []
    archive = json.loads((folder / 'list.json').read_text(encoding='utf-8'))
    for order, (school_year, collection, title, kim) in enumerate(archive, 1):
        raw = folder / 'raw' / f'{kim}.json'
        if refresh or not raw.exists():
            try:
                raw.write_bytes(fetch(f'https://kompege.ru/api/v1/variant/kim/{kim}'))
            except Exception as exc:
                skipped.append({'kim': kim, 'title': title, 'reason': str(exc)})
                continue
        data = json.loads(raw.read_text(encoding='utf-8'))
        if str(data.get('kim')) != str(kim) or not isinstance(data.get('tasks'), list) or not data['tasks']:
            raise ValueError(f'Unexpected variant data: {kim}')
        new_layout = any(int(t['number']) == 110 for t in data['tasks'])
        items = []
        for position, entry in enumerate(data['tasks'], 1):
            kind, ident = int(entry['number']), int(entry['taskId'])
            topic = topic_of(kind)
            number = EXAM_NUMBER.get(kind, topic) if new_layout else topic
            key = answer_rows(entry.get('key'))
            if not key or len(key) > 250:
                raise ValueError(f'No usable answer: variant {kim}, task {ident}')
            table = entry.get('table') or {}
            rows, cols = int(table.get('rows') or 1), int(table.get('cols') or 1)
            if not (1 <= rows <= 20 and 1 <= cols <= 10):
                raise ValueError(f'Unexpected answer table: {kim}/{ident}')
            items.append({'position': position, 'number': number, 'topic': topic, 'source_type': kind,
                          'source_id': ident, 'answer': key, 'rows': rows, 'cols': cols})
            public = kind <= 27 and (topic, ident) in catalog
            if public or (topic, ident) in tasks:
                continue
            common = {'source_id': ident, 'source_type': kind, 'source_url': f'https://kompege.ru/task?id={ident}',
                      'author': str(entry.get('comment') or '').strip()[:200],
                      'difficulty': LEVELS.get(entry.get('difficulty') if isinstance(entry.get('difficulty'), int) else None, 'medium'),
                      'answer': answer_text(str(entry.get('key') or '').replace('\\n', ' ')), 'answer_origin': 'variant', 'files': []}
            if not common['answer']:
                raise ValueError(f'No task answer: {kim}/{ident}')
            if topic in (20, 21):
                condition = f'19-{ident}-condition'
                if (19, ident) not in tasks:
                    raise ValueError(f'Follow-up {topic} without its 19 in variant {kim}: {ident}')
                question = f'{topic}-{ident}-question'
                render(entry['text'], question)
                jobs.append({'id': ident, 'number': topic, 'page': question + '.html', 'image': 'captures/' + question + '.jpg'})
                tasks[(topic, ident)] = dict(common, number=topic, images=['screenshots/' + condition + '.jpg', 'screenshots/' + question + '.jpg'])
                continue
            name = f'{topic:02}-{ident}-condition'
            render(entry['text'], name)
            jobs.append({'id': ident, 'number': topic, 'page': name + '.html', 'image': 'captures/' + name + '.jpg'})
            for index, item in enumerate(entry.get('files') or []):
                original = safe_name(item['name'])
                if Path(original).suffix.lower() not in FILE_TYPES:
                    raise ValueError('Unsupported original file: ' + original)
                relative = f'attachments/{ident}/{index:02}-{original}'
                destination = folder / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                url = source_url(item['url'])
                if not destination.exists():
                    destination.write_bytes(fetch(url, MAX_DATA_FILE))
                common['files'].append({'path': relative, 'name': original, 'url': url,
                                        'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()})
            tasks[(topic, ident)] = dict(common, number=topic, images=['screenshots/' + name + '.jpg'])
        hide = str(data.get('hideAnswer')) == 'True'
        variants.append({'kim': int(kim), 'title': title, 'school_year': school_year, 'exam_year': exam_year(school_year),
                         'collection': collection, 'description': str(data.get('description') or '').strip()[:180],
                         'hide_answers': hide, 'order': order, 'source_url': f'https://kompege.ru/variant?kim={kim}', 'items': items})
        if len(variants) % 10 == 0:
            print(f'Prepared {len(variants)} variants, {len(tasks)} hidden tasks...', flush=True)
    rows = list(tasks.values())
    counts = Counter(row['number'] for row in rows)
    digest = hashlib.sha256((folder / 'list.json').read_bytes()).hexdigest()
    manifest = {'format': FORMAT, 'source': 'https://kompege.ru', 'created_at': datetime.now(timezone.utc).isoformat(),
                'rights': 'User confirmed permission to republish.', 'numbers': sorted(counts),
                'source_counts': {str(n): c for n, c in sorted(counts.items())}, 'source_sha256': {str(n): digest for n in sorted(counts)},
                'tasks': rows, 'variants': variants, 'skipped_variants': skipped, 'missing_images': sorted(set(missing_images))}
    (folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (folder / 'jobs.json').write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Prepared {len(variants)} variants ({len(skipped)} unavailable), {len(rows)} hidden tasks, '
          f'{sum(len(r["files"]) for r in rows)} files, {len(jobs)} pages; missing images: {len(set(missing_images))}.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--catalog', nargs='+', required=True, help='Folders of imported bank packages (manifest.json)')
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    prepare(args.folder, args.catalog, args.refresh)
