"""Prepare complete public categories with exact source conditions and original files."""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from PIL import Image
try:
    from .condition_render import Condition, EXTERNAL_IMAGES, HEAD, TAIL
    from .prepare_kompege_pilot import answer_text, fetch, safe_name, source_url, MAX_ASSET
except ImportError:
    from condition_render import Condition, EXTERNAL_IMAGES, HEAD, TAIL
    from prepare_kompege_pilot import answer_text, fetch, safe_name, source_url, MAX_ASSET

FORMAT = 'infocode-kompege-numbers-v1'
MAX_DATA_FILE = 50 * 1024 * 1024  # matches TaskBlock.file limit

# Source task 31372 has an empty API key but provides the author's calculation.
# Independently checked using exact fractions: stereo/mono PCM, then 34% reduction.
# Do not execute downloaded solution code or reuse the recovery after source edits.
ANSWER_RECOVERIES = {
    (7, 31372): {'answer': '120',
        'condition_sha256': 'cb262b4df9c455d1f449c8b79978a3e97e0067ae5b18e6b377ae35015222f170',
        'solution_sha256': 'c35b0440b9a1745eda337947a3b8759d04fe6dd1b89379bbbbad8930cda1db36'},
}


def source_answer(row):
    answer = answer_text(row.get('key'))
    if answer:
        return answer, 'api'
    recovery = ANSWER_RECOVERIES.get((row.get('number'), row.get('taskId')))
    if recovery and hashlib.sha256(row['text'].encode()).hexdigest() == recovery['condition_sha256'] and hashlib.sha256((row.get('solve_text') or '').encode()).hexdigest() == recovery['solution_sha256']:
        return recovery['answer'], 'author-solution'
    raise ValueError('No verified source answer: ' + str(row.get('taskId')))


def image_resolver(folder):
    """Download condition illustrations once into source-images/ and return local paths."""
    cache = {}

    def local_image(url):
        if url.startswith('data:'):
            return url
        if url not in cache:
            stem = folder / 'source-images' / hashlib.sha256(url.encode()).hexdigest()[:20]
            suffix = Path(urlsplit(url).path).suffix
            existing = [p for p in stem.parent.glob(stem.name + '.*')] if not suffix else []
            destination = stem.with_suffix(suffix) if suffix else (existing[0] if existing else None)
            if destination is None or not destination.exists():
                if any(re.fullmatch(pattern, url) for pattern in EXTERNAL_IMAGES):
                    with urlopen(Request(url, headers={'User-Agent':'Infocode licensed educational import/1.0'}), timeout=40) as response:
                        if response.url != url:
                            raise ValueError('Unexpected illustration redirect')
                        data = response.read(MAX_ASSET + 1)
                else:
                    data = fetch(url, MAX_ASSET)
                if len(data) > MAX_ASSET:
                    raise ValueError('Oversized illustration')
                with Image.open(io.BytesIO(data)) as image:
                    kind = image.format
                    image.verify()
                if destination is None:
                    extension = {'PNG': '.png', 'GIF': '.gif', 'JPEG': '.jpg'}.get(kind)
                    if not extension:
                        raise ValueError('Unsupported illustration format: ' + url)
                    destination = stem.with_suffix(extension)
                destination.write_bytes(data)
            cache[url] = destination.relative_to(folder).as_posix()
        return cache[url]

    return local_image


def prepare(folder, numbers, refresh=False):
    numbers = sorted(set(numbers))
    if not numbers or any(n not in range(1, 28) for n in numbers):
        raise ValueError('Choose EGE categories 1–27')
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'captures').mkdir(exist_ok=True)
    (folder / 'source-images').mkdir(exist_ok=True)
    local_image = image_resolver(folder)

    tasks, jobs, counts, hashes = [], [], {}, {}
    for number in numbers:
        source = folder / f'number-{number:02}.json'
        if refresh or not source.exists():
            source.write_bytes(fetch(f'https://kompege.ru/api/v1/task/number/{number}'))
        rows = json.loads(source.read_text(encoding='utf-8'))
        hashes[str(number)] = hashlib.sha256(source.read_bytes()).hexdigest()
        seen = set()
        for row in sorted(rows, key=lambda r: -r['taskId']):
            if row['hide']:
                continue
            ident = row['taskId']
            answer, answer_origin = source_answer(row)
            if row['number'] != number or ident in seen or not answer or len(answer) > 250 or not row['text']:
                raise ValueError('Invalid public source row: ' + str(ident))
            seen.add(ident)
            parser = Condition(image_resolver=local_image)
            parser.feed(row['text'])
            parser.close()
            name = f'{number:02}-{ident}-condition'
            (folder / (name + '.html')).write_text(HEAD + ''.join(parser.parts) + TAIL, encoding='utf-8')
            jobs.append({'id': ident, 'number': number, 'page': name + '.html', 'image': 'captures/' + name + '.jpg'})
            files = []
            for index, item in enumerate(row.get('files') or []):
                name_original = safe_name(item['name'])
                if Path(name_original).suffix.lower() not in ('.txt','.csv','.xlsx','.xls','.pdf','.zip','.docx','.ods','.odt','.py'):
                    raise ValueError('Unsupported original file: ' + name_original)
                relative = f'attachments/{ident}/{index:02}-{name_original}'
                destination = folder / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                url = source_url(item['url'])
                if not destination.exists():
                    destination.write_bytes(fetch(url, MAX_DATA_FILE))
                files.append({'path': relative, 'name': name_original, 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(), 'url': url})
            tasks.append({'number': number, 'source_id': ident, 'source_url': f'https://kompege.ru/task?id={ident}',
                'author': str(row.get('comment') or '').strip(), 'answer': answer, 'answer_origin': answer_origin,
                'difficulty': {0:'easy',1:'medium',2:'hard',3:'grave'}[row['difficulty']],
                'images': ['screenshots/' + name + '.jpg'], 'files': files})
            if len(seen) % 25 == 0:
                print(f'Number {number}: prepared {len(seen)} tasks...', flush=True)
        counts[str(number)] = len(seen)
    manifest = {'format': FORMAT, 'source': 'https://kompege.ru', 'numbers': numbers,
        'created_at': datetime.now(timezone.utc).isoformat(), 'source_counts': counts, 'source_sha256': hashes,
        'rights': 'User confirmed permission to republish.', 'tasks': tasks}
    (folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (folder / 'jobs.json').write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Prepared {counts}; {sum(len(t["files"]) for t in tasks)} original files; native 1920px screenshots required.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--numbers', nargs='+', type=int, required=True)
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    prepare(args.folder.resolve(), args.numbers, args.refresh)
