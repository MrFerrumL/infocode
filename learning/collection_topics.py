"""Topics inside an exam number, as Yandex Uchebnik splits its collections ("№ 5 — двоичная система
счисления", "№ 7 — звуки", "№ 19-21 — повторение с двумя кучами"…).

Conditions on the site are screenshots, so tasks are tagged once from the saved source texts
(deploy/build_task_topics.py → learning/data/task_topics.json); the collections builder then uses the tags.
"""
import re

X, Y = r'(?<![a-zа-я])[xх](?![a-zа-я])', r'(?<![a-zа-я])[yу](?![a-zа-я])'

# number → ordered (tag, pattern); the first match wins
RULES = {
    5: [('other_base', r'троичн|четверичн|пятеричн|шестеричн|семеричн|восьмеричн|девятеричн|шестнадцатеричн|основани'),
        ('binary', r'двоичн'), ('decimal', r'.')],
    7: [('transfer', r'передач|канал связи|скорост'), ('audio', r'звук|аудио|оцифров|моно|стерео|музык|дискретизац'),
        ('image', r'изображ|пиксел|цвет|растров|фотограф|палитр|рисун')],
    8: [('list', r'алфавитном порядке|пронумерован|список')],
    14: [('long', r'\^|степен|\*\*'), ('subst', X + r'|неизвестн|переменн'), ('eq', r'.')],
    15: [('div', r'дел\s*\('), ('bit', r'&|поразрядн'), ('seg', r'отрез'), ('sets', r'множеств'),
         ('graph', X + r'[\s\S]*' + Y + '|' + Y + r'[\s\S]*' + X)],
    19: [('two', r'две кучи|двух куч|двумя кучами|кучах'), ('one', r'куч')],
    22: [('shift', r'сдвиг|задерж|отлож')],
    25: [('mask', r'маск|\?|\*'), ('div', r'делител')],
    26: [('greedy', r'жадн'), ('sort', r'сортир|упорядоч')],
}

# words of a Yandex collection title → topic tag ('!tag' = tasks without that tag)
TITLE_TOPICS = {
    5: [('двоичная', 'binary'), ('десятичная', 'decimal'), ('другие системы', 'other_base')],
    7: [('передача', 'transfer'), ('звук', 'audio'), ('аудио', 'audio'), ('изображ', 'image')],
    8: [('упорядоченный список', 'list'), ('комбинаторика', '!list')],
    14: [('подстановка', 'subst'), ('уравнения', 'eq'), ('длинная', 'long')],
    15: [('множества делителей', 'div'), ('множества', 'sets'), ('арифметические', 'bit'), ('отрезки', 'seg'), ('графики', 'graph')],
    19: [('одной кучей', 'one'), ('двумя кучами', 'two')],
    22: [('без сдвигов', '!shift'), ('со сдвигами', 'shift')],
    25: [('делители', 'div'), ('маски', 'mask')],
    26: [('сортировка', 'sort'), ('жадный', '!sort')],
}

# words of a title → how tasks are chosen
MODES = [('все официальные', 'exam_all'), ('уровень егэ', 'exam'), ('проще егэ', 'easy_old'), ('повторение', 'exam'),
         ('на подумать', 'hard'), ('решаем кодом', 'hard'), ('кодом', 'hard'), ('аналитически', 'medium'),
         ('знакомство', 'easy')]


def topic(number, text):
    text = (text or '').lower()
    for tag, pattern in RULES.get(number, []):
        if re.search(pattern, text):
            return tag
    return ''


def plan_of(number, level):
    """('binary', 'exam') for 'двоичная система счисления'-like level texts."""
    level = level.lower()
    tag = next((tag for words, tag in TITLE_TOPICS.get(number, []) if words in level), '')
    mode = next((mode for words, mode in MODES if words in level), 'any')
    return tag, mode
