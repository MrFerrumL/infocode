"""Build KOMPEGE notes for already imported tasks (input for `manage.py label_source_tasks`).

Reads the saved source data: number-*.json bank snapshots, number 1 source-records.json, and raw
variant data (raw/<kim>.json). 20 and 21 take the note of their 19.
"""
import argparse
import glob
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from learning.sources import classify, label  # noqa: E402


def topic_of(kind):
    return kind if kind <= 27 else kind % 100


def build(folders, output):
    notes = {}

    def add(topic, ident, comment, text):
        entry = {'system': 'kompege', 'topic': topic, 'id': ident, 'label': label(comment, text), 'kind': classify(comment)}
        notes.setdefault((topic, ident), entry)
        if topic == 19:
            for follow in (20, 21):
                notes.setdefault((follow, ident), dict(entry, topic=follow))

    for folder in folders:
        folder = Path(folder)
        files = glob.glob(str(folder / 'number-*.json')) + glob.glob(str(folder / 'source-records.json'))
        for name in files:
            for row in json.loads(Path(name).read_text(encoding='utf-8')):
                if str(row.get('hide')) != 'True':
                    add(int(row['number']), int(row['taskId']), row.get('comment'), row.get('text'))
        for name in glob.glob(str(folder / 'raw' / '*.json')):
            for row in json.loads(Path(name).read_text(encoding='utf-8')).get('tasks', []):
                kind = int(row['number'])
                if kind not in (20, 21):
                    add(topic_of(kind), int(row['taskId']), row.get('comment'), row.get('text'))
    rows = sorted(notes.values(), key=lambda r: (r['topic'], r['id']))
    Path(output).write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    kinds = {}
    for row in rows:
        kinds[row['kind']] = kinds.get(row['kind'], 0) + 1
    print(f'{len(rows)} notes: {kinds}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output')
    parser.add_argument('folders', nargs='+')
    args = parser.parse_args()
    build(args.folders, args.output)
