"""Prepare the complete public, licensed number-1 bank; capture with native browser next."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from condition_render import Condition, HEAD, TAIL
from prepare_kompege_pilot import answer_text, fetch


def prepare(folder, refresh=False):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'captures').mkdir(exist_ok=True)
    source = folder / 'source-records.json'
    if refresh or not source.exists():
        source.write_bytes(fetch('https://kompege.ru/api/v1/task/number/1'))
    rows = json.loads(source.read_text(encoding='utf-8'))
    tasks, jobs, seen = [], [], set()
    for row in sorted(rows, key=lambda r: -r['taskId']):
        ident = row['taskId']
        if row['hide']:
            continue
        answer = answer_text(row['key'])
        if row['number'] != 1 or ident in seen or not answer or len(answer) > 250 or not row['text']:
            raise ValueError('Invalid public source row: ' + str(ident))
        # Fail rather than silently drop any original accompanying files.
        if row.get('files'):
            raise ValueError('Source now contains attachments; extend file packaging before importing.')
        seen.add(ident)
        parser = Condition()
        parser.feed(row['text'])
        parser.close()
        name = f'01-{ident}-condition'
        (folder / (name + '.html')).write_text(HEAD + ''.join(parser.parts) + TAIL, encoding='utf-8')
        jobs.append({'id': ident, 'page': name + '.html', 'image': 'captures/' + name + '.jpg'})
        tasks.append({'number': 1, 'source_id': ident, 'source_url': f'https://kompege.ru/task?id={ident}',
                      'author': str(row.get('comment') or '').strip(), 'answer': answer,
                      'difficulty': {0:'easy', 1:'medium', 2:'hard', 3:'grave'}[row['difficulty']],
                      'images': ['screenshots/' + name + '.jpg'], 'files': []})
    manifest = {'format': 'infocode-kompege-number1-v1', 'source': 'https://kompege.ru',
                'created_at': datetime.now(timezone.utc).isoformat(),
                'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'source_count': len(tasks), 'rights': 'User confirmed permission to republish.', 'tasks': tasks}
    (folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (folder / 'jobs.json').write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Prepared all {len(tasks)} public number-1 tasks; native 1920px screenshots required.', flush=True)


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('folder', type=Path)
    cli.add_argument('--refresh', action='store_true')
    args = cli.parse_args()
    prepare(args.folder.resolve(), args.refresh)
