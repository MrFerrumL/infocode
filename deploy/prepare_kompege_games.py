"""Prepare KOMPEGE game tasks 19–21: one shared condition and two follow-up questions per group.

On the source site number 19 carries numbers 20 and 21 as sub-tasks. Each group becomes three
linked tasks with the same source ID: 19 = condition, 20/21 = condition + own question.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
try:
    from .condition_render import Condition, HEAD, TAIL
    from .prepare_kompege_numbers import image_resolver, source_label
    from .prepare_kompege_pilot import answer_text, fetch
except ImportError:
    from condition_render import Condition, HEAD, TAIL
    from prepare_kompege_numbers import image_resolver, source_label
    from prepare_kompege_pilot import answer_text, fetch

FORMAT = 'infocode-kompege-games-v1'
NUMBERS = [19, 20, 21]
LEVELS = {0: 'easy', 1: 'medium', 2: 'hard', 3: 'grave'}


def render(folder, local_image, text, name):
    parser = Condition(image_resolver=local_image)
    parser.feed(text)
    parser.close()
    (folder / (name + '.html')).write_text(HEAD + ''.join(parser.parts) + TAIL, encoding='utf-8')


def prepare(folder, refresh=False):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'captures').mkdir(exist_ok=True)
    (folder / 'source-images').mkdir(exist_ok=True)
    local_image = image_resolver(folder)
    source = folder / 'number-19.json'
    if refresh or not source.exists():
        source.write_bytes(fetch('https://kompege.ru/api/v1/task/number/19'))
    rows = json.loads(source.read_text(encoding='utf-8'))
    tasks, jobs, seen = [], [], set()
    for row in sorted(rows, key=lambda r: -r['taskId']):
        if row['hide']:
            continue
        ident = row['taskId']
        subs = sorted(row.get('subTask') or [], key=lambda s: int(s.get('number', 0)))
        if row['number'] != 19 or ident in seen or not row['text'] or [int(s.get('number', 0)) for s in subs] != [20, 21]:
            raise ValueError('Invalid public game group: ' + str(ident))
        seen.add(ident)
        common = {'source_id': ident, 'source_url': f'https://kompege.ru/task?id={ident}',
                  'author': str(row.get('comment') or '').strip(), 'label': source_label(row.get('comment'), row['text']),
                  'difficulty': LEVELS[row['difficulty']], 'files': []}
        condition = f'19-{ident}-condition'
        render(folder, local_image, row['text'], condition)
        jobs.append({'id': ident, 'number': 19, 'page': condition + '.html', 'image': 'captures/' + condition + '.jpg'})
        entries = [(19, row['key'], [condition])]
        for sub in subs:
            number = int(sub['number'])
            question = f'{number}-{ident}-question'
            render(folder, local_image, sub['text'], question)
            jobs.append({'id': ident, 'number': number, 'page': question + '.html', 'image': 'captures/' + question + '.jpg'})
            entries.append((number, sub['key'], [condition, question]))
        for number, key, images in entries:
            answer = answer_text(key)
            if not answer or len(answer) > 250:
                raise ValueError(f'No source answer: {ident} / {number}')
            tasks.append(dict(common, number=number, answer=answer, answer_origin='api',
                              images=['screenshots/' + name + '.jpg' for name in images]))
        if len(seen) % 25 == 0:
            print(f'Prepared {len(seen)} groups...', flush=True)
    counts = {str(n): len(seen) for n in NUMBERS}
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = {'format': FORMAT, 'source': 'https://kompege.ru', 'numbers': NUMBERS,
        'created_at': datetime.now(timezone.utc).isoformat(), 'source_counts': counts,
        'source_sha256': {str(n): digest for n in NUMBERS},
        'rights': 'User confirmed permission to republish.', 'tasks': tasks}
    (folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (folder / 'jobs.json').write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Prepared {len(seen)} groups 19–21; {len(jobs)} pages to capture.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    prepare(args.folder.resolve(), args.refresh)
