"""Prepare one number of K. Polyakov's EGE task collection (the DOC from kpolyakov.spb.ru) for import.

Inputs: the DOC converted to PDF by LibreOffice (soffice --headless --convert-to pdf), the answers
table (answers.xls from the site), the data archive (e.g. 17data.zip) and the KOMPEGE bank snapshot of
the same number. Each training task is cut from the PDF as a 1920px image exactly as typeset in Word
(tasks spanning a page break are joined); tasks already in KOMPEGE (same answer, same task) are skipped.

Needs PyMuPDF, pandas and xlrd (not used by the site itself).
"""
import argparse
import difflib
import hashlib
import html
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import pandas
import pymupdf
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from learning.sources import classify  # noqa: E402

FORMAT = 'infocode-polyakov-doc-v1'
SOURCE = 'https://kpolyakov.spb.ru'
PAGE_URL = 'https://kpolyakov.spb.ru/school/ege.htm'
WIDTH = 1920
TOP, BOTTOM = 50.5, 782  # just below the header rule (y≈49.3), above the footer rule (y≈790)


def normal_text(value):
    value = html.unescape(re.sub(r'<[^>]+>', ' ', value or ''))
    value = re.sub(r'^\s*\([^()]{2,60}\)', '', value.strip())
    value = re.sub(r'\b\d*-?\d+\.txt\b|файл[еа]?\b', '', value.lower())
    return ' '.join(re.sub(r'[^0-9a-zа-яё]+', ' ', value).split())


def normal_answer(value):
    return ' '.join(str(value).replace('\\n', ' ').split()).lower()


def task_texts(pdf, number):
    """Plain text of each training task, keyed by its number in the collection."""
    text = '\n'.join(page.get_text() for page in pdf)
    start = text.lower().find('задачи для тренировки')
    if start < 0:
        raise ValueError('No "Задачи для тренировки" section')
    parts = re.split(r'(?m)^\s*(\d{1,3})\)\s', text[start:])[1:]
    tasks = {int(parts[i]): parts[i + 1] for i in range(0, len(parts), 2)}
    if sorted(tasks) != list(range(1, len(tasks) + 1)):
        raise ValueError('Training tasks are not numbered 1..N')
    return tasks


def task_starts(pdf, count):
    """(page, y) of the line where each task 'N)' begins, scanning in order after the training section."""
    starts, expected, started = {}, 1, False
    for pno, page in enumerate(pdf):
        for block in page.get_text('dict')['blocks']:
            for line in block.get('lines', []):
                text = ''.join(span['text'] for span in line['spans']).strip()
                if not started:
                    started = 'задачи для тренировки' in text.lower()
                    continue
                # Word list numbering comes out as a separate line '17)'.
                if expected <= count and re.match(rf'{expected}\)(\s|$)', text):
                    starts[expected] = (pno, line['bbox'][1])
                    expected += 1
    if len(starts) != count:
        raise ValueError(f'Found {len(starts)} of {count} task starts in the PDF')
    return starts


def content_bottom(page):
    ys = [b[3] for b in page.get_text('blocks') if b[3] <= BOTTOM + 1]
    return min(BOTTOM, max(ys) + 4) if ys else BOTTOM


def cut(pdf, begin, end, left, right):
    """Render [begin, end) — possibly over several pages — as one image WIDTH pixels wide."""
    zoom = WIDTH / (right - left)
    pieces, (pno, y), (end_page, end_y) = [], begin, end
    while True:
        bottom = end_y if pno == end_page else content_bottom(pdf[pno])
        if bottom - y > 2:
            clip = pymupdf.Rect(left, y, right, bottom)
            pix = pdf[pno].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
            pieces.append(Image.frombytes('RGB', (pix.width, pix.height), pix.samples).resize((WIDTH, pix.height)))
        if pno == end_page:
            break
        pno, y = pno + 1, TOP
    height = sum(p.height for p in pieces)
    image = Image.new('RGB', (WIDTH, height), 'white')
    offset = 0
    for piece in pieces:
        image.paste(piece, (0, offset))
        offset += piece.height
    data = io.BytesIO()
    image.save(data, 'JPEG', quality=92)
    return data.getvalue()


def prepare(number, pdf_path, answers_path, data_path, kompege_path, output):
    pdf = pymupdf.open(pdf_path)
    tasks = task_texts(pdf, number)
    table = pandas.read_excel(answers_path, sheet_name=0, header=None)
    column = [i for i, h in enumerate(table.iloc[0]) if str(h).strip() in (str(number), f'{number}.0')]
    if len(column) != 1:
        raise ValueError(f'No answer column for number {number}')
    cells = {int(table.iloc[r, 0]): str(table.iloc[r, column[0]])
             for r in range(1, len(table)) if pandas.notna(table.iloc[r, 0]) and pandas.notna(table.iloc[r, column[0]])}
    # Many cells end with a link to the video solution: the answer is what precedes it.
    videos = {n: m.group(0) for n, cell in cells.items() if (m := re.search(r'https?://\S+', cell))}
    answers = {n: ' '.join(re.sub(r'https?://\S+', ' ', cell).split()) for n, cell in cells.items()}
    bad = {n: a for n, a in answers.items() if not re.fullmatch(r'-?\d+(?:[.,]\d+)?(?: -?\d+(?:[.,]\d+)?)*', a)}
    if bad:
        raise ValueError(f'Unexpected answers: {list(bad.items())[:5]}')
    missing = sorted(set(tasks) - set(answers))
    if missing:
        raise ValueError(f'No answers for tasks {missing[:10]}')
    # KOMPEGE already has many of these tasks: skip a task when the answer matches and the text is the same task.
    kompege = [r for r in json.loads(Path(kompege_path).read_text(encoding='utf-8')) if not r['hide']]
    by_answer = {}
    for row in kompege:
        by_answer.setdefault(normal_answer(row['key']), []).append(row)
    duplicates = {}
    for n, body in tasks.items():
        for row in by_answer.get(normal_answer(answers[n]), []):
            ratio = difflib.SequenceMatcher(None, normal_text(body)[:1500], normal_text(row['text'])[:1500]).ratio()
            if ratio >= 0.45:
                duplicates[n] = {'kompege': row['taskId'], 'similarity': round(ratio, 2)}
                break
    archive = zipfile.ZipFile(data_path)
    data_files = {Path(name).name: name for name in archive.namelist() if not name.endswith('/')}
    starts = task_starts(pdf, len(tasks))
    words = [w for p in pdf for w in p.get_text('words') if TOP < w[1] < BOTTOM]
    left, right = min(w[0] for w in words) - 6, max(w[2] for w in words) + 6
    last = (pdf.page_count - 1, content_bottom(pdf[pdf.page_count - 1]))
    rows, blobs = [], {}
    for n, body in tasks.items():
        if n in duplicates:
            continue
        # Cut tight to the task lines so no descenders of the neighbouring task show up.
        begin = (starts[n][0], max(TOP, starts[n][1] - 0.5))
        end = (starts[n + 1][0], starts[n + 1][1] - 0.5) if n + 1 in starts else last
        image = cut(pdf, begin, end, left, right)
        image_path = f'screenshots/{number:02}-{n}-condition.jpg'
        blobs[image_path] = image
        files = []
        for name in dict.fromkeys(re.findall(rf'\b{number}-\d+[a-zA-Z]?\.txt\b', body)):
            if name not in data_files:
                raise ValueError(f'Task {n} needs {name}, which is not in the data archive')
            data = archive.read(data_files[name])
            path = f'attachments/{number}-{n}/{name}'
            blobs[path] = data
            files.append({'path': path, 'name': name, 'sha256': hashlib.sha256(data).hexdigest()})
        note = re.match(r'\s*\(([^()]{2,60})\)', body)
        note = note.group(1).strip() if note else ''
        rows.append({'number': number, 'source_id': n, 'source_url': PAGE_URL, 'author': note[:200],
                     'label': (note or 'Авторская · К. Поляков')[:120], 'kind': classify(note),
                     'answer': answers[n], 'answer_origin': 'answers.xls', 'difficulty': 'medium',
                     'solution': f'Видеоразбор: {videos[n]}' if n in videos else '',
                     'images': [{'path': image_path, 'sha256': hashlib.sha256(image).hexdigest()}], 'files': files})
    manifest = {'format': FORMAT, 'source': SOURCE, 'numbers': [number], 'source_counts': {str(number): len(rows)},
                'source_sha256': {str(number): hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()},
                'rights': 'Added by the platform owner from the public collection.', 'skipped_as_kompege': duplicates, 'tasks': rows}
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        for path, data in blobs.items():
            bundle.writestr(path, data)
    print(f'{len(tasks)} tasks: {len(rows)} packaged, {len(duplicates)} already in KOMPEGE; '
          f'{sum(len(r["files"]) for r in rows)} data files; {Path(output).stat().st_size} bytes.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('number', type=int)
    parser.add_argument('pdf')
    parser.add_argument('answers')
    parser.add_argument('data')
    parser.add_argument('kompege', help='KOMPEGE bank snapshot number-NN.json')
    parser.add_argument('output')
    args = parser.parse_args()
    prepare(args.number, args.pdf, args.answers, args.data, args.kompege, args.output)
