"""Tag imported tasks with topics (learning/collection_topics.py) from the saved KOMPEGE source texts.

Writes learning/data/task_topics.json: {"kompege": {"5": {"31112": "binary", …}, …}}.
Usage: python deploy/build_task_topics.py <folders with number-NN.json snapshots>
"""
import glob
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from learning.collection_topics import RULES, topic  # noqa: E402


def main(folders):
    tags = {}
    for folder in folders:
        for name in glob.glob(str(Path(folder) / 'number-*.json')):
            for row in json.loads(Path(name).read_text(encoding='utf-8')):
                number = int(row['number'])
                if number not in RULES or str(row.get('hide')) == 'True':
                    continue
                text = html.unescape(re.sub(r'<[^>]+>', ' ', row.get('text') or ''))
                tag = topic(number, text)
                if tag:
                    tags.setdefault(str(number), {})[str(row['taskId'])] = tag
    output = ROOT / 'learning' / 'data' / 'task_topics.json'
    output.write_text(json.dumps({'kompege': tags}, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    print({number: len(items) for number, items in sorted(tags.items(), key=lambda kv: int(kv[0]))})


if __name__ == '__main__':
    main(sys.argv[1:])
