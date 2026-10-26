"""Prepare several numbers of K. Polyakov's EGE collection (kpolyakov.spb.ru/school/ege.htm) as one import bundle.

Same approach as prepare_polyakov_doc.py (number 17), for every document of the page:
each training task is cut from the DOC (converted to PDF by LibreOffice) as a 1920px picture exactly as
typeset, answers come from answers.xls, data files from the number's archive, and tasks already in the
KOMPEGE bank are skipped. Extra documents of one number keep Polyakov's numbering: "7-2.15" is stored
as task 2015 of number 7, "7-v.3" as 3003 (see learning/sources.py, polyakov_ref).

Numbers are Polyakov's, which follow the current exam (ЕГЭ-2027: IP masks are 10, counting programs 13, graphs 23)
as KOMPEGE's do; a document whose first page names another number is refused.

Numbers 19–21 come as game groups: Polyakov writes the questions once per section ("Вопросы 19-21 к
следующим задачам"), so every task gets the condition plus the section's question 19, 20 or 21.

    python deploy/prepare_polyakov_bank.py OUT.zip conv/ src/answers.xls KOMPEGE_DIR 1 2 3 7 7-2 7-v ...
    python deploy/prepare_polyakov_bank.py OUT.zip conv/ src/answers.xls KOMPEGE_DIR 1921

Needs PyMuPDF, pandas and xlrd (not used by the site itself).
"""
import argparse
import difflib
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

import pandas
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import io  # noqa: E402
from PIL import Image  # noqa: E402
from deploy.prepare_polyakov_doc import BOTTOM, TOP, WIDTH, normal_answer, normal_text  # noqa: E402
from learning.sources import POLYAKOV_PARTS, classify, polyakov_header_number  # noqa: E402

FORMAT = 'infocode-polyakov-doc-v1'
GAMES_FORMAT = 'infocode-polyakov-games-v1'
SOURCE = 'https://kpolyakov.spb.ru'
PAGE_URL = 'https://kpolyakov.spb.ru/school/ege.htm'
DATA = r'[\w-]+\.(?:txt|xlsx|xls|csv|ods|odt|docx)'
SECTION = re.compile(r'^Вопросы\s+19\s*-\s*21')


RULES = {}


def rules(page):
    """(top, bottom) of the page body: between the header and footer rules, which differ between documents."""
    key = (page.parent.name, page.number)
    if key not in RULES:
        RULES[key] = measure(page)
    return RULES[key]


def measure(page):
    lines = [d['rect'] for d in page.get_drawings() if d['rect'].height < 1.5 and d['rect'].width > 200]
    # The header rule is the topmost line, the footer rule the lowest (tables and mazes have lines too).
    top = min((r.y1 for r in lines if r.y1 < 70), default=TOP - 1.2) + 0.5
    bottom = max((r.y0 for r in lines if r.y0 > page.rect.height - 70), default=BOTTOM + 8) - 0.5
    return top, bottom


def content_bottom(page):
    top, bottom = rules(page)
    ys = [b[3] for b in page.get_text('blocks') if top < (b[1] + b[3]) / 2 < bottom]
    return min(bottom, max(ys) + 3) if ys else bottom


def cut(pdf, begin, end, left, right):
    """Render [begin, end) — possibly over several pages — as one image WIDTH pixels wide."""
    zoom = WIDTH / (right - left)
    pieces, (pno, y), (end_page, end_y) = [], begin, end
    y = max(y, rules(pdf[pno])[0])
    while True:
        bottom = min(end_y, content_bottom(pdf[pno])) if pno == end_page else content_bottom(pdf[pno])
        if bottom - y > 2:
            pix = pdf[pno].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=pymupdf.Rect(left, y, right, bottom), alpha=False)
            pieces.append(Image.frombytes('RGB', (pix.width, pix.height), pix.samples).resize((WIDTH, pix.height)))
        if pno >= end_page:
            break
        pno += 1
        y = rules(pdf[pno])[0]
    if not pieces:
        raise ValueError(f'Empty cut {begin} → {end}')
    height = sum(p.height for p in pieces)
    if height > 30000:
        raise ValueError(f'Cut {begin} → {end} is {height}px tall')
    image = Image.new('RGB', (WIDTH, height), 'white')
    offset = 0
    for piece in pieces:
        image.paste(piece, (0, offset))
        offset += piece.height
    data = io.BytesIO()
    image.save(data, 'JPEG', quality=92)
    return data.getvalue()


def parse_key(key):
    """'7-2' → (number 7, part offset 2000, answer column '7-2'); '1921' → games."""
    if key == '1921':
        return 19, 0, '19'
    number, _, part = key.partition('-')
    offset = {v: k for k, v in POLYAKOV_PARTS.items()}[part] * 1000 if part else 0
    return int(number), offset, key


def lines_after_training(pdf):
    started = False
    for pno, page in enumerate(pdf):
        for block in page.get_text('dict')['blocks']:
            for line in block.get('lines', []):
                text = ''.join(span['text'] for span in line['spans']).strip()
                if not started:
                    started = 'задачи для тренировки' in text.lower()
                    continue
                # A line at the very top of a page starts just above the header rule's y, so judge by its middle.
                top, bottom = rules(page)
                if top < (line['bbox'][1] + line['bbox'][3]) / 2 < bottom:
                    yield pno, line['bbox'][0], line['bbox'][1], text


def training_tasks(pdf):
    """{n: {'start': (page, y), 'end': (page, y) | None, 'text': str}} by the list number at the left margin.

    Answer options "1) … 4)" inside a task are indented, Word's list numbers are right-aligned at the margin.
    A "Вопросы 19-21" heading ends the task before it."""
    tasks, margin, current, expected = {}, None, None, 1
    sections = []
    for pno, x, y, text in lines_after_training(pdf):
        match = re.match(r'(\d{1,3})\)', text)
        if match and int(match.group(1)) == expected and (margin is None or x <= margin + 8):
            margin = x if margin is None else min(margin, x)
            current = tasks[expected] = {'start': (pno, y), 'end': None, 'text': text[match.end():]}
            expected += 1
        elif SECTION.match(text):
            if current and current['end'] is None:
                current['end'] = (pno, y)
            sections.append({'start': (pno, y), 'first': expected, 'lines': []})
            current = None
        elif current is not None:
            current['text'] += '\n' + text
        if sections and current is None:
            sections[-1]['lines'].append((pno, y, text))
    return tasks, sections


def answers_column(table, column):
    heads = [str(h).strip().removesuffix('.0') for h in table.iloc[0]]
    index = heads.index(column)
    cells = {int(table.iloc[r, 0]): str(table.iloc[r, index]) for r in range(1, len(table))
             if pandas.notna(table.iloc[r, 0]) and pandas.notna(table.iloc[r, index])}
    videos = {n: m.group(0) for n, cell in cells.items() if (m := re.search(r'https?://\S+', cell))}
    answers = {n: ' '.join(re.sub(r'https?://\S+', ' ', cell).split()) for n, cell in cells.items()}
    return answers, videos


def kompege_rows(kompege_dir, number):
    path = next(Path(kompege_dir).glob(f'kompege-*/number-{number:02}.json'), None) or next(Path(kompege_dir).glob(f'kompege-*/number-{number}.json'))
    return [r for r in json.loads(path.read_text(encoding='utf-8')) if str(r.get('hide')).lower() not in ('true', '1')]


def numbers_in(value):
    return set(re.findall(r'\d+', normal_text(value)))


def duplicates_of(tasks, answers, kompege, threshold=0.45, extra=None):
    by_answer = {}
    for row in kompege:
        by_answer.setdefault(normal_answer(row['key']), []).append(row)
    found = {}
    for n, task in tasks.items():
        for row in by_answer.get(normal_answer(answers.get(n, '')), []):
            if extra and not extra(n, row):
                continue
            ratio = difflib.SequenceMatcher(None, normal_text(task['text'])[:1500], normal_text(row['text'])[:1500]).ratio()
            # Same answer and similar wording is not enough (many tasks share a template): the numbers must match too.
            ours, theirs = numbers_in(task['text']), numbers_in(row['text'])
            overlap = len(ours & theirs) / max(1, len(ours | theirs))
            if ratio >= threshold and (overlap >= 0.75 if len(ours | theirs) >= 3 else ratio >= 0.85):
                found[n] = {'kompege': row['taskId'], 'similarity': round(ratio, 2)}
                break
    return found


class Bundle:
    def __init__(self):
        self.rows, self.blobs, self.skipped, self.warnings, self.hashes, self.counts = [], {}, {}, [], {}, {}

    def add_blob(self, path, data):
        self.blobs[path] = data
        return {'path': path, 'sha256': hashlib.sha256(data).hexdigest()}


def frame(pdf):
    words = [w for p in pdf for w in p.get_text('words') if rules(p)[0] < (w[1] + w[3]) / 2 < rules(p)[1]]
    return min(w[0] for w in words) - 6, max(w[2] for w in words) + 6


def task_end(pdf, tasks, n):
    if tasks[n]['end']:
        return tasks[n]['end']
    if n + 1 in tasks:
        return tasks[n + 1]['start'][0], tasks[n + 1]['start'][1] - 0.5
    return pdf.page_count - 1, content_bottom(pdf[pdf.page_count - 1])


def data_index(paths):
    """{lower-case file name: (archive, member)}; zips inside the archive (24data.zip → k7data.zip) are opened too."""
    index = {}
    def add(archive):
        for member in archive.namelist():
            if member.endswith('/'):
                continue
            if member.lower().endswith('.zip'):
                add(zipfile.ZipFile(io.BytesIO(archive.read(member))))
            else:
                index.setdefault(Path(member).name.lower(), (archive, member))
    for path in paths:
        add(zipfile.ZipFile(path))
    return index


def data_files(src, number, body, archive_names, bundle, ident):
    files = []
    for name in dict.fromkeys(re.findall(DATA, body)):
        if name.lower() not in archive_names and f'{number}-{name}'.lower() in archive_names:
            name = f'{number}-{name}'  # "18-" and "99.xls" on two lines
        if name.lower() not in archive_names:
            bundle.warnings.append(f'{number}.{ident}: {name} is mentioned but not in a data archive')
            continue
        archive, member = archive_names[name.lower()]
        name = Path(member).name
        data = archive.read(member)
        item = bundle.add_blob(f'attachments/{number}-{ident}/{name}', data)
        files.append({**item, 'name': name})
    return files


def add_document(bundle, key, conv, table, src, kompege_dir):
    number, offset, column = parse_key(key)
    pdf_path = Path(conv) / f'ege{key}.pdf'
    pdf = pymupdf.open(pdf_path)
    header = polyakov_header_number(pdf[0].get_text())
    if header is not None and header != number:
        raise ValueError(f'{key}: the document is headed ЕГЭ №{header}, not {number}')
    tasks, sections = training_tasks(pdf)
    if sorted(tasks) != list(range(1, len(tasks) + 1)) or not tasks:
        raise ValueError(f'{key}: training tasks are not numbered 1..N')
    answers, videos = answers_column(table, column)
    missing = sorted(set(tasks) - set(answers))
    if missing:
        raise ValueError(f'{key}: no answers for tasks {missing[:10]}')
    archives = data_index(sorted(Path(src).glob(f'{number}data*.zip')))
    if len(tasks) < len(answers):
        raise ValueError(f'{key}: found {len(tasks)} tasks, the answers table has {len(answers)}')
    skipped = duplicates_of(tasks, answers, kompege_rows(kompege_dir, number))
    bundle.skipped[key] = skipped
    left, right = frame(pdf)
    count = 0
    for n, task in tasks.items():
        if n in skipped:
            continue
        ident = offset + n
        image = cut(pdf, (task['start'][0], task['start'][1] - 0.5), task_end(pdf, tasks, n), left, right)
        note = re.match(r'\s*\(([^()]{2,60})\)', task['text'])
        note = note.group(1).strip() if note else ''
        bundle.rows.append({'number': number, 'source_id': ident, 'source_url': PAGE_URL, 'author': note[:200],
                            'label': (note or 'Авторская · К. Поляков')[:120], 'kind': classify(note),
                            'answer': answers[n][:250], 'answer_origin': 'answers.xls', 'difficulty': 'medium',
                            'solution': f'Видеоразбор: {videos[n]}' if n in videos else '',
                            'images': [bundle.add_blob(f'screenshots/{key}-{n}-condition.jpg', image)],
                            'files': data_files(src, number, task['text'], archives, bundle, ident)})
        if not re.fullmatch(r'-?\d+(?:[.,]\d+)?(?: -?\d+(?:[.,]\d+)?)*', answers[n]):
            bundle.warnings.append(f'{key}.{n}: non-numeric answer {answers[n]!r}')
        count += 1
    bundle.counts[number] = bundle.counts.get(number, 0) + count
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    bundle.hashes[number] = hashlib.sha256((bundle.hashes.get(number, '') + digest).encode()).hexdigest()
    print(f'{key}: {len(tasks)} tasks, {count} packaged, {len(skipped)} already in KOMPEGE', flush=True)


def question_blocks(pdf, section, reference):
    """[(start, end)] of "Задание 19/20/21" in a section, or in the example task it refers to."""
    lines = section['lines']
    if any(re.match(r'Задание\s+19', t) for _, _, t in lines):
        marks = [(p, y) for p, y, t in lines if re.match(r'Задание\s+(19|20|21)\b', t)]
        end = None  # up to the first task of the section
    else:
        marks, end = reference
    return marks, end


def reference_questions(pdf):
    """Questions of example P-00: from "Задание 19" after it to the line "Решение"."""
    marks, end, seen = [], None, False
    for pno, page in enumerate(pdf):
        for block in page.get_text('dict')['blocks']:
            for line in block.get('lines', []):
                text = ''.join(span['text'] for span in line['spans']).strip()
                if not seen:
                    seen = bool(re.match(r'[РP]-?00\b', text))
                    continue
                if len(marks) < 3 and re.match(rf'Задание\s+{19 + len(marks)}\b', text):
                    marks.append((pno, line['bbox'][1]))
                elif len(marks) == 3 and text.startswith('Решение'):
                    return marks, (pno, line['bbox'][1] - 0.5)
    raise ValueError('Example P-00 questions not found')


def add_games(bundle, conv, table, kompege_dir):
    pdf_path = Path(conv) / 'ege1921.pdf'
    pdf = pymupdf.open(pdf_path)
    tasks, sections = training_tasks(pdf)
    if sorted(tasks) != list(range(1, len(tasks) + 1)):
        raise ValueError('1921: training tasks are not numbered 1..N')
    columns = {q: answers_column(table, str(q)) for q in (19, 20, 21)}
    reference = reference_questions(pdf)
    left, right = frame(pdf)
    pictures = []  # per section: the three question pictures
    for index, section in enumerate(sections):
        marks, end = question_blocks(pdf, section, reference)
        if len(marks) != 3:
            raise ValueError(f'Section {index + 1}: expected questions 19, 20, 21')
        first = tasks[section['first']]['start']
        end = end or (first[0], first[1] - 0.5)
        stops = [(p, y - 0.5) for p, y in marks[1:]] + [end]
        pictures.append([cut(pdf, (p, y - 0.5), stop, left, right) for (p, y), stop in zip(marks, stops)])
    kompege = kompege_rows(kompege_dir, 19)
    sub = lambda row: [normal_answer(s.get('key', '')) for s in eval_list(row.get('subTask'))]
    skipped = duplicates_of(tasks, columns[19][0], kompege, 0.3,
                            extra=lambda n, row: sub(row)[:2] == [normal_answer(columns[20][0].get(n, '')), normal_answer(columns[21][0].get(n, ''))])
    bundle.skipped['1921'] = skipped
    count = 0
    for n, task in tasks.items():
        if n in skipped:
            continue
        section = max(i for i, s in enumerate(sections) if s['first'] <= n)
        condition = bundle.add_blob(f'screenshots/1921-{n}-condition.jpg',
                                    cut(pdf, (task['start'][0], task['start'][1] - 0.5), task_end(pdf, tasks, n), left, right))
        note = re.match(r'\s*\(([^()]{2,60})\)', task['text'])
        note = note.group(1).strip() if note else ''
        for q in (19, 20, 21):
            answers, videos = columns[q]
            if n not in answers:
                raise ValueError(f'1921.{n}: no answer for question {q}')
            question = bundle.add_blob(f'screenshots/1921-s{section + 1}-q{q}.jpg', pictures[section][q - 19])
            bundle.rows.append({'number': q, 'source_id': n, 'source_url': PAGE_URL, 'author': note[:200],
                                'label': (note or 'Авторская · К. Поляков')[:120], 'kind': classify(note),
                                'answer': answers[n][:250], 'answer_origin': 'answers.xls', 'difficulty': 'medium',
                                'solution': f'Видеоразбор: {videos[n]}' if n in videos and q == 19 else '',
                                'images': [condition, question], 'files': []})
        count += 1
    for q in (19, 20, 21):
        bundle.counts[q] = count
        bundle.hashes[q] = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    print(f'1921: {len(tasks)} tasks in {len(sections)} sections, {count} groups packaged, {len(skipped)} already in KOMPEGE', flush=True)


def eval_list(value):
    import ast
    try:
        return ast.literal_eval(value) if isinstance(value, str) else (value or [])
    except (ValueError, SyntaxError):
        return []


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('output')
    parser.add_argument('conv', help='Folder with egeN.pdf')
    parser.add_argument('answers', help='answers.xls')
    parser.add_argument('kompege', help='Folder with kompege-*/number-NN.json snapshots')
    parser.add_argument('docs', nargs='+', help='Document keys: 1 2 7 7-2 7-v … or 1921')
    args = parser.parse_args()
    table = pandas.read_excel(args.answers, sheet_name=0, header=None)
    src = Path(args.answers).parent
    bundle = Bundle()
    games = args.docs == ['1921']
    if games:
        add_games(bundle, args.conv, table, args.kompege)
    else:
        for key in args.docs:
            add_document(bundle, key, args.conv, table, src, args.kompege)
    numbers = sorted(bundle.counts)
    manifest = {'format': GAMES_FORMAT if games else FORMAT, 'source': SOURCE, 'numbers': numbers,
                'source_counts': {str(n): bundle.counts[n] for n in numbers}, 'source_sha256': {str(n): bundle.hashes[n] for n in numbers},
                'rights': 'Added by the platform owner from the public collection.', 'skipped_as_kompege': bundle.skipped,
                'tasks': bundle.rows}
    with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_DEFLATED) as out:
        out.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=1))
        for path, data in bundle.blobs.items():
            out.writestr(path, data)
    Path(args.output).with_suffix('.warnings.txt').write_text('\n'.join(bundle.warnings), encoding='utf-8')
    print(f'{len(bundle.rows)} rows, {sum(len(r["files"]) for r in bundle.rows)} data files, {len(bundle.warnings)} warnings; '
          f'{Path(args.output).stat().st_size // 1024 // 1024} MB.')


if __name__ == '__main__':
    main()
