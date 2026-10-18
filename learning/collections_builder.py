"""Build the collections section: Yandex Uchebnik collections as links, and the platform's own sets.

Own sets follow the same scheme as Yandex ("знакомство", "на подумать", "повторение") and are drawn
deterministically from the KOMPEGE and K. Polyakov tasks in the catalogue, without overlaps.
"""
import json
import re
from pathlib import Path
from django.db import transaction
from django.db.models import Case, F, IntegerField, Value, When
from .collection_models import Collection, CollectionItem
from .game_groups import FOLLOW_UPS, grouped_follow_up
from .models import Task

YANDEX_LIST = Path(__file__).resolve().parent / 'data' / 'yandex_collections.json'
YANDEX_URL = 'https://education.yandex.ru/ege/inf/collections/{}/task/1?start=1'
LEVELS = [
    ('intro', 'знакомство', 'Простые задачи, чтобы разобраться в номере.'),
    ('think', 'на подумать', 'Задачи посложнее — для уверенной тренировки.'),
    ('review', 'повторение', 'Задачи реальных ЕГЭ, демоверсий и ЕГКР.'),
]
NUMBERS = [n for n in range(1, 28) if n not in FOLLOW_UPS]  # 19 stands for the 19–21 group


def parse_yandex(title):
    match = re.match(r'№\s*(\d+)(?:\s*-\s*21)?\s*(?:—\s*)?(.*)', title.strip())
    if not match:
        raise ValueError('Unexpected collection title: ' + title)
    return int(match.group(1)), match.group(2).strip()


def sync_yandex():
    rows = json.loads(YANDEX_LIST.read_text(encoding='utf-8'))
    for order, (title, ident) in enumerate(rows, 1):
        number, level = parse_yandex(title)
        Collection.objects.update_or_create(key=f'yandex:{ident}', defaults={
            'source': 'yandex', 'number': number, 'title': ' '.join(title.split()), 'level': level,
            'url': YANDEX_URL.format(ident), 'sort_order': order, 'description': 'Подборка Яндекс Учебника'})
    return len(rows)


def pool(number):
    newest = Case(When(source_system='kompege', then=F('external_id')), default=Value(0), output_field=IntegerField())
    polyakov_first = Case(When(source_system='polyakov', then=F('external_id')), default=Value(0), output_field=IntegerField())
    return (Task.objects.filter(published=True, in_catalog=True, topic__number=number, source_system__in=['kompege', 'polyakov'])
            .exclude(grouped_follow_up()).order_by(newest.desc(), polyakov_first, 'pk'))


def pick(queryset, taken, size):
    chosen = [pk for pk in queryset.values_list('pk', flat=True) if pk not in taken][:size]
    taken.update(chosen)
    return chosen


def build_own(size=10):
    built, keys = 0, set()
    for number in NUMBERS:
        tasks, taken = pool(number), set()
        # Real exam tasks are kept for "повторение"; the other levels draw from the rest.
        plans = {'review': pick(tasks.filter(source_kind__in=['exam', 'demo', 'probe']), taken, size)}
        plans['intro'] = pick(tasks.filter(difficulty='easy'), taken, size)
        plans['think'] = pick(tasks.filter(difficulty__in=['hard', 'grave', 'ege']), taken, size)
        if len(plans['intro']) < size:  # K. Polyakov's first tasks of a number are introductory
            plans['intro'] += pick(tasks.filter(source_system='polyakov'), taken, size - len(plans['intro']))
        if len(plans['intro']) < size:
            plans['intro'] += pick(tasks.filter(difficulty='medium'), taken, size - len(plans['intro']))
        if len(plans['think']) < size:
            plans['think'] += pick(tasks.filter(difficulty='medium'), taken, size - len(plans['think']))
        label = '19–21' if number == 19 else str(number)
        for order, (code, name, description) in enumerate(LEVELS, 1):
            ids = plans[code]
            key = f'infocode:{number}:{code}'
            if not ids:
                Collection.objects.filter(key=key).delete()
                continue
            collection, _ = Collection.objects.update_or_create(key=key, defaults={
                'source': 'infocode', 'number': number, 'title': f'№ {label} — {name}', 'level': name,
                'description': description, 'url': '', 'sort_order': order})
            collection.items.all().delete()
            CollectionItem.objects.bulk_create([CollectionItem(collection=collection, task_id=pk, position=i) for i, pk in enumerate(ids, 1)])
            keys.add(key)
            built += 1
    # Sets from earlier layouts (e.g. named like Yandex's) go away.
    Collection.objects.filter(source='infocode').exclude(key__in=keys).delete()
    return built


@transaction.atomic
def sync_all(size=10):
    return sync_yandex(), build_own(size)
