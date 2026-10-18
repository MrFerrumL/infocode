"""Find similar tasks with a neural text model for the "Похожие" button (learning/similar_tasks.py).

Conditions on the site are pictures, so the texts come from the saved sources: the KOMPEGE bank
snapshots (kompege-*/number-NN.json) and K. Polyakov's DOC files converted to PDF. Every condition is
embedded by a multilingual sentence-embedding model; for each task the nearest tasks of the same exam
number (19–21 as one) are kept.

    python deploy/build_similar_tasks.py WORK_DIR learning/data/similar_tasks.json.gz

WORK_DIR holds kompege-*/number-NN.json, polyakov-all/conv/egeN.pdf and polyakov17/conv/ege17.pdf.
Needs sentence-transformers, torch, pymupdf and pandas (not used by the site itself).
"""
import argparse
import gzip
import html
import json
import re
import sys
from pathlib import Path

import numpy
import pymupdf
from sentence_transformers import SentenceTransformer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from deploy.prepare_polyakov_bank import parse_key, training_tasks  # noqa: E402

MODEL = 'intfloat/multilingual-e5-small'
KEEP = 12
POLYAKOV_DOCS = ['1', '2', '3', '4', '5', '6', '7', '7-2', '7-v', '8', '9', '10', '11', '12', '13', '14', '15', '16',
                 '17', '18', '1921', '22', '23', '24', '25']


def plain(value):
    value = html.unescape(re.sub(r'<[^>]+>', ' ', value or ''))
    value = re.sub(r'\\[()\[\]]', ' ', value)  # MathJax brackets
    return ' '.join(value.split())[:2000]


def kompege_texts(work):
    texts = {}
    for path in sorted(Path(work).glob('kompege-*/number-*.json')):
        for row in json.loads(path.read_text(encoding='utf-8')):
            number = int(row['number'])
            if str(row.get('hide')).lower() in ('true', '1') or number in (20, 21):
                continue
            text = plain(row.get('text'))
            if text:
                texts[f'kompege:{number}:{int(row["taskId"])}'] = (number, text)
    return texts


def polyakov_texts(work):
    texts = {}
    for key in POLYAKOV_DOCS:
        folder = Path(work) / ('polyakov17' if key == '17' else 'polyakov-all') / 'conv'
        pdf = folder / f'ege{key}.pdf'
        if not pdf.exists():
            print('missing', pdf)
            continue
        number, offset, _ = parse_key(key)
        tasks, _ = training_tasks(pymupdf.open(pdf))
        for n, task in tasks.items():
            texts[f'polyakov:{number}:{offset + n}'] = (number, plain(task['text']))
    return texts


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('work')
    parser.add_argument('output')
    args = parser.parse_args()
    texts = {**kompege_texts(args.work), **polyakov_texts(args.work)}
    print(f'{len(texts)} conditions', flush=True)
    model = SentenceTransformer(MODEL)
    keys = list(texts)
    vectors = model.encode(['query: ' + texts[k][1] for k in keys], batch_size=64, normalize_embeddings=True, show_progress_bar=True)
    by_number = {}
    for i, key in enumerate(keys):
        by_number.setdefault(texts[key][0], []).append(i)
    neighbours = {}
    for number, rows in sorted(by_number.items()):
        matrix = vectors[rows] @ vectors[rows].T
        numpy.fill_diagonal(matrix, -1)
        matrix[matrix > 0.995] = -1  # the very same task stored twice is not a "similar" one
        for r, i in enumerate(rows):
            best = numpy.argsort(-matrix[r])[:KEEP]
            neighbours[keys[i]] = [[keys[rows[j]], round(float(matrix[r, j]), 3)] for j in best if matrix[r, j] > 0]
        print(f'number {number}: {len(rows)} tasks', flush=True)
    data = {'model': MODEL, 'keep': KEEP, 'neighbours': neighbours}
    Path(args.output).write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode(), 9))
    print(f'{len(neighbours)} tasks → {Path(args.output).stat().st_size // 1024} KB')


if __name__ == '__main__':
    main()
