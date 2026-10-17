"""Prepare only five public, answered KOMPEGE tasks per EGE number for a licensed pilot.

Screenshots are supplied separately by the browser capture step. No account, token,
database or server credentials are read by this tool.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


SITE = 'https://kompege.ru'
PER_NUMBER = 5
MAX_ASSET = 20 * 1024 * 1024


class AnswerText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ('br', 'p', 'div', 'tr'):
            self.parts.append(' ')


def answer_text(value):
    parser = AnswerText()
    parser.feed(str(value or '').replace('\\n', ' '))
    return ' '.join(''.join(parser.parts).split())


def source_url(value):
    url = urljoin(SITE + '/', value)
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname not in ('kompege.ru', 'www.kompege.ru') or parts.username or parts.port not in (None, 443):
        raise ValueError('Unexpected attachment origin: ' + url)
    return url


def fetch(url, maximum=32 * 1024 * 1024):
    for attempt in range(3):
        try:
            with urlopen(Request(source_url(url), headers={'User-Agent': 'Infocode-Licensed-Pilot/1.0', 'Accept': '*/*'}), timeout=40) as response:
                source_url(response.url)
                data = response.read(maximum + 1)
            if len(data) > maximum:
                raise ValueError('Asset exceeds the supported size: ' + url)
            return data
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            time.sleep(min(20, max(2, int(exc.headers.get('Retry-After', '2')))))


def safe_name(value):
    name = str(value or '').replace('\\', '/').rsplit('/', 1)[-1]
    name = re.sub(r'[^\w.() -]', '_', name, flags=re.UNICODE).strip(' .')
    if not name or name in ('.', '..') or len(name) > 90:
        raise ValueError('Invalid attachment filename')
    return name


def select_rows(rows):
    selected = []
    seen = set()
    for row in sorted(rows, key=lambda item: int(item.get('taskId', 0)), reverse=True):
        task_id = int(row.get('taskId', 0))
        answer = answer_text(row.get('key'))
        if task_id <= 0 or task_id in seen or row.get('hide') or not row.get('text') or not answer or len(answer) > 250:
            continue
        if row.get('number') == 19:
            subs = {sub.get('number'): sub for sub in row.get('subTask') or []}
            if any(not answer_text(subs.get(number, {}).get('key')) for number in (20, 21)):
                continue
        seen.add(task_id)
        selected.append(row)
        if len(selected) == PER_NUMBER:
            break
    if len(selected) != PER_NUMBER:
        raise ValueError('Fewer than five suitable public tasks in a source category')
    return selected


def prepare(folder):
    folder.mkdir(parents=True, exist_ok=True)
    raw_folder = folder / 'source-records'
    raw_folder.mkdir(exist_ok=True)
    tasks, captures = [], []
    for number in [*range(1, 20), *range(22, 28)]:
        raw_path = raw_folder / f'{number:02}.json'
        if raw_path.exists():
            rows = select_rows(json.loads(raw_path.read_text(encoding='utf-8')))
        else:
            rows = select_rows(json.loads(fetch(f'{SITE}/api/v1/task/number/{number}')))
            raw_path.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
        for row in rows:
            task_id = int(row['taskId'])
            screenshot = f'screenshots/{number:02}-{task_id}-condition.png'
            captures.append({'id': task_id, 'number': number, 'url': f'{SITE}/task?id={task_id}', 'image': screenshot})
            attachments = []
            for index, item in enumerate(row.get('files') or []):
                name = safe_name(item.get('name'))
                url = source_url(item['url'])
                relative = f'attachments/{task_id}/{index:02}-{name}'
                destination = folder / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.exists():
                    data = fetch(url, MAX_ASSET)
                    destination.write_bytes(data)
                digest = hashlib.sha256(destination.read_bytes()).hexdigest()
                attachments.append({'path': relative, 'name': name, 'sha256': digest, 'url': url})
            for target in ((19, 20, 21) if number == 19 else (number,)):
                answer = row['key'] if target == number else next(sub['key'] for sub in row['subTask'] if sub['number'] == target)
                images = [screenshot]
                if target != number:
                    images.append(f'screenshots/{number:02}-{task_id}-sub{target}.png')
                tasks.append({'number': target, 'source_id': task_id, 'source_url': f'{SITE}/task?id={task_id}',
                              'author': str(row.get('comment') or '').strip(), 'answer': answer_text(answer),
                              'difficulty': {0:'easy', 1:'medium', 2:'hard', 3:'grave'}.get(row.get('difficulty'), 'ege'),
                              'images': images, 'files': attachments})
        print(f'Number {number:02}: selected 5; total pilot cards {len(tasks)}', flush=True)
    tasks.sort(key=lambda row: (row['number'], -row['source_id']))
    if len(tasks) != 135 or any(sum(row['number'] == number for row in tasks) != 5 for number in range(1, 28)):
        raise ValueError('Pilot must have exactly five cards per number (135 total).')
    manifest = {'format': 'infocode-kompege-pilot-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
                'source': SITE, 'rights': 'User confirmed permission to republish this pilot.',
                'tasks': tasks, 'captures': captures}
    (folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Prepared {len(tasks)} cards, {len(captures)} source pages. Browser screenshots are still required.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    prepare(args.folder.resolve())


if __name__ == '__main__':
    main()
