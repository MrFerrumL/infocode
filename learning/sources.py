"""Where a task comes from: the source site and the kind of material, labelled as on KOMPEGE.

KOMPEGE prints a short note above each task ("Демоверсия 2027", "Основная волна 18.06.26",
"Джобс 12.03.2025"); tasks without it usually name their author at the start of the condition.
"""
import re

# Task.source_system → site shown in the catalogue filter. New banks (K. Polyakov, Yandex) add a line here.
SITES = {'kompege': 'КЕГЭ', 'polyakov': 'К. Поляков', 'fipi': 'ФИПИ', 'yandex': 'Яндекс', 'infocode-gen': 'Инфокод · генератор', '': 'Свои задачи'}

# K. Polyakov's extra documents of one number: task 2015 of number 7 is "7-2.15", 3003 is "7-v.3".
POLYAKOV_PARTS = {2: '2', 3: 'v'}


def polyakov_ref(number, ident):
    """Task number as on K. Polyakov's site: '17.5', '7-2.15', '19-21.4'."""
    if not ident:
        return str(number)
    part, n = divmod(ident, 1000)
    if number in (19, 20, 21):
        return f'19-21.{ident}'
    return f'{number}-{POLYAKOV_PARTS[part]}.{n}' if part in POLYAKOV_PARTS else f'{number}.{ident}'


KINDS = [('demo', 'Демоверсия'), ('exam', 'Реальный ЕГЭ'), ('probe', 'Пробники и ЕГКР'), ('author', 'Авторские')]

EXAM = ('основная волна', 'резерв', 'пересдача', 'досрочн', 'егэ-20', 'егэ 20')
PROBE = ('егкр', 'апробация', 'открытый вариант', 'фипи', 'openfipi', 'статград', 'пробник имц')


def classify(note):
    text = (note or '').strip().lower()
    if text.startswith('демоверсия'):
        return 'demo'
    if text.startswith(EXAM):
        return 'exam'
    if text.startswith(PROBE):
        return 'probe'
    return 'author'


def author_in_condition(html):
    """'(Л. Шастин) Исполнитель…' → 'Л. Шастин'."""
    match = re.match(r'\s*(?:<[^>]+>\s*)*\(([^()<>]{2,60})\)', html or '')
    return match.group(1).strip() if match else ''


def label(note, html=''):
    note = (note or '').strip()
    if note:
        return note[:120]
    author = author_in_condition(html)
    return ('Авторская · ' + author)[:120] if author else 'Авторская'
