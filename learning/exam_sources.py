"""Where an exam task comes from: demo, approbation, a wave of the exam, ЕГКР, an open variant, the FIPI bank…

The note of the source bank (KOMPEGE: «Основная волна 18.06.26», «Апробация 04.03.26») or the bracket at the start
of the condition (K. Polyakov: «(Апробация-2026)», «(ЕГЭ-2025)») tells it. A task without either that is a copy of
a labelled task (the same text by its sentence vector and the same answer) gets the copy's source.
Tasks from official sources form the «ЕГЭ» difficulty level; their own level is kept in original_difficulty.
"""
import re

RULES = [  # (code, pattern) — the first match wins
    ('demo', r'демоверси|\bдемо[-\s]?\d|\(демо'),
    ('approbation', r'апробаци'),
    ('early', r'досрочн'),
    ('reserve', r'резерв'),
    ('retake', r'пересдач'),
    ('egkr', r'егкр'),
    ('open', r'открытый вариант'),
    ('main', r'основн\w* (волн|период)'),
    ('statgrad', r'статград'),
    ('fipi', r'открытый банк фипи|openfipi|\bфипи\b'),
    ('exam', r'\bегэ[-\s]?20\d\d\b|реальн\w* егэ'),
]
COMPILED = [(code, re.compile(pattern, re.I)) for code, pattern in RULES]
OFFICIAL = {'demo', 'approbation', 'early', 'main', 'exam', 'reserve', 'retake', 'egkr', 'open', 'fipi'}
LEADING = re.compile(r'^\s*(?:\d{1,4}\)\s*)?\*?\s*\(([^()]{2,60})\)')
COPY = 0.985


def classify(label, text=''):
    """Source code from the bank's note, else from the bracket opening the condition; '' when neither says."""
    for code, pattern in COMPILED:
        if label and pattern.search(label):
            return code
    found = LEADING.match(text or '')
    if found:
        for code, pattern in COMPILED:
            if pattern.search(found.group(1)):
                return code
    return ''


def apply(task, code):
    """Set the source and the «ЕГЭ» level that follows from it; returns True when something changed."""
    before = (task.exam_source, task.difficulty, task.original_difficulty)
    task.exam_source = code
    if code in OFFICIAL:
        if task.difficulty != 'ege':
            task.original_difficulty, task.difficulty = task.difficulty, 'ege'
    elif task.original_difficulty:
        task.difficulty, task.original_difficulty = task.original_difficulty, ''
    return before != (task.exam_source, task.difficulty, task.original_difficulty)


def copies(unlabelled, labelled):
    """{task id: source} for unlabelled tasks whose text and answer equal a labelled task's (same exam number)."""
    from .embeddings import matrix
    found = {}
    by_number = {}
    for pk, number, answer, vector, code in labelled:
        if vector is not None:
            by_number.setdefault(number, []).append((pk, answer, vector, code))
    for pk, number, answer, vector in unlabelled:
        group = by_number.get(number)
        if not group or vector is None:
            continue
        scores = matrix([bytes(v) for _, _, v, _ in group]) @ matrix([bytes(vector)])[0]
        best = int(scores.argmax())
        if scores[best] > COPY and ' '.join(group[best][1].split()) == ' '.join(answer.split()):
            found[pk] = group[best][3]
    return found
