"""Build the platform's collections, named and split like Yandex Uchebnik's prepared collections.

The list of collection names comes from learning/data/yandex_collections.json (titles only — no content
is taken from Yandex). Each collection is filled deterministically with KOMPEGE and K. Polyakov tasks
from the catalogue: by topic (learning/collection_topics.py) and by level ("знакомство", "на подумать",
"повторение"…), without repeating a task inside one exam number. Numbers missing from the list get
the default three levels.
"""
import json
import re
from pathlib import Path
from django.db import transaction
from django.db.models import Case, F, IntegerField, Value, When
from .collection_models import Collection, CollectionItem
from .collection_topics import plan_of
from .game_groups import FOLLOW_UPS, grouped_follow_up
from .models import Task

DATA = Path(__file__).resolve().parent / 'data'
NAMES = DATA / 'yandex_collections.json'
TOPICS = DATA / 'task_topics.json'
NUMBERS = [n for n in range(1, 28) if n not in FOLLOW_UPS]  # 19 stands for the 19–21 group
DEFAULT_LEVELS = [('знакомство', 'Простые задачи, чтобы разобраться в номере.'),
                  ('на подумать', 'Задачи посложнее — для уверенной тренировки.'),
                  ('повторение', 'Задачи реальных ЕГЭ, демоверсий и ЕГКР.')]
DESCRIPTIONS = {'easy': 'Простые задачи, чтобы разобраться.', 'easy_old': 'Самые простые задачи — проще, чем на ЕГЭ.',
                'hard': 'Задачи посложнее — для уверенной тренировки.', 'medium': 'Задачи среднего уровня.',
                'exam': 'Задачи реальных ЕГЭ, демоверсий и ЕГКР.', 'exam_all': 'Все задачи реальных ЕГЭ, демоверсий и ЕГКР.',
                'any': 'Задачи по теме из банков КЕГЭ и К. Полякова.'}
EXAM_KINDS = ['exam', 'demo', 'probe']
LEVEL_RANK = {'easy': 0, 'medium': 1, 'hard': 2, 'ege': 2, 'grave': 3}


def parse_title(title):
    match = re.match(r'№\s*(\d+)(?:\s*-\s*21)?\s*(?:—\s*)?(.*)', title.strip())
    if not match:
        raise ValueError('Unexpected collection title: ' + title)
    return int(match.group(1)), match.group(2).strip()


def clean_title(title):
    # The collection is ours, so a Yandex author credit does not belong in its name.
    title = re.sub(r'\s+от\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+$', '', ' '.join(title.split()))
    return re.sub(r'^№\s*(\d)', r'№ \1', title)


def load_names():
    return json.loads(NAMES.read_text(encoding='utf-8'))


def load_topics():
    return json.loads(TOPICS.read_text(encoding='utf-8')) if TOPICS.exists() else {}


def plans():
    """Ordered (key, number, title, level, tag, mode) — named like Yandex, plus defaults for missing numbers."""
    rows, seen = [], set()
    for ident_title, ident in load_names():
        number, level = parse_title(ident_title)
        tag, mode = plan_of(number, level)
        title = clean_title(ident_title)
        rows.append((f'infocode:y:{ident}', number, title, parse_title(title)[1], tag, mode))
        seen.add(number)
    for number in NUMBERS:
        if number not in seen:
            label = '19–21' if number == 19 else str(number)
            for level, mode in (('знакомство', 'easy'), ('на подумать', 'hard'), ('повторение', 'exam')):
                rows.append((f'infocode:{number}:{mode}', number, f'№ {label} — {level}', level, '', mode))
    return rows


def pool(number):
    newest = Case(When(source_system='kompege', then=F('external_id')), default=Value(0), output_field=IntegerField())
    polyakov_first = Case(When(source_system='polyakov', then=F('external_id')), default=Value(0), output_field=IntegerField())
    return list(Task.objects.filter(published=True, in_catalog=True, topic__number=number, source_system__in=['kompege', 'polyakov'])
                .exclude(grouped_follow_up()).order_by(newest.desc(), polyakov_first, 'pk')
                .values('pk', 'source_system', 'external_id', 'difficulty', 'source_kind'))


def choose(tasks, tag, mode, tags, taken, size):
    def has_tag(task):
        own = tags.get((task['source_system'], task['external_id']), '')
        return own != tag[1:] if tag.startswith('!') else (not tag or own == tag)
    candidates = [t for t in tasks if t['pk'] not in taken and has_tag(t)]
    exam = [t for t in candidates if t['source_kind'] in EXAM_KINDS]
    rest = [t for t in candidates if t['source_kind'] not in EXAM_KINDS]
    level = lambda items, *names: [t for t in items if t['difficulty'] in names]
    # Each level tops up only from the neighbouring level, so one set never drains another.
    order = {'exam': exam or rest, 'exam_all': exam,
             'easy': level(rest, 'easy') + level(rest, 'medium'), 'easy_old': level(list(reversed(rest)), 'easy') + level(list(reversed(rest)), 'medium'),
             'hard': level(rest, 'grave', 'hard', 'ege') + level(rest, 'medium'), 'medium': level(rest, 'medium') + level(rest, 'easy'),
             'any': rest + exam}[mode]
    limit = 40 if mode == 'exam_all' else size
    chosen = list(dict.fromkeys(t['pk'] for t in order))[:limit]
    taken.update(chosen)
    return chosen


def build_own(size=10):
    stored = load_topics()
    tags = {(system, int(ident)): tag for system, numbers in stored.items() for items in numbers.values() for ident, tag in items.items()}
    rows, built, keys = plans(), 0, set()
    by_number = {}
    for order, row in enumerate(rows, 1):
        by_number.setdefault(row[1], []).append((order, row))
    for number, items in by_number.items():
        tasks, taken, chosen = pool(number), set(), {}
        # Exam sets first, so "повторение" keeps the real exam tasks; then topics and levels.
        priority = {'exam_all': 0, 'exam': 1, 'hard': 2, 'medium': 3, 'easy_old': 4, 'easy': 5, 'any': 6}
        for order, (key, _, _, _, tag, mode) in sorted(items, key=lambda item: (bool(not item[1][4]), priority[item[1][5]])):
            chosen[key] = choose(tasks, tag, mode, tags, taken, size)
        for order, (key, _, title, level, tag, mode) in items:
            if not chosen[key]:
                continue
            collection, _ = Collection.objects.update_or_create(key=key, defaults={
                'source': 'infocode', 'number': number, 'title': title, 'level': level, 'url': '',
                'description': DESCRIPTIONS[mode], 'sort_order': order, 'published': True})
            collection.items.all().delete()
            CollectionItem.objects.bulk_create([CollectionItem(collection=collection, task_id=pk, position=i) for i, pk in enumerate(chosen[key], 1)])
            keys.add(key)
            built += 1
    Collection.objects.filter(source='infocode').exclude(key__in=keys).delete()
    return built


@transaction.atomic
def sync_all(size=10):
    # Yandex collections are no longer listed as links.
    removed, _ = Collection.objects.filter(source='yandex').delete()
    return removed, build_own(size)
