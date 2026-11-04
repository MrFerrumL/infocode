"""№ 17 («последовательность в файле»): what a task enumerates, pairs, triples or quadruples of elements.

The condition says it in one word («определите количество пар / троек / четвёрок элементов последовательности…»).
The kind is kept in the task's subtopic, so the catalogue and the КР picker filter by plain equality. A subtopic a
teacher typed is never changed; only the marks made here are rewritten when the rules change.
"""
import re

# key (the `tuple` filter value) → (subtopic written to the task, label of the filter)
KINDS = {'2': ('Перебор пар', 'Двойки (пары)'), '3': ('Перебор троек', 'Тройки'), '4': ('Перебор четвёрок', 'Четвёрки')}
SUBTOPICS = {key: subtopic for key, (subtopic, _) in KINDS.items()}
CHOICES = [(key, label) for key, (_, label) in KINDS.items()]
PAIR_ENDINGS = r'(?:а|ы|у|е|ой|ам|ами|ах)'  # пара, пары, паре…
GROUP_ENDINGS = r'(?:а|и|у|е|ой|ам|ами|ах)'  # тройка, тройки, тройке…
# After «ё» is written as «е»: a word on its own, so «параметр» or «на троих» never count.
WORD_PATTERNS = {'2': rf'\b(?:пар{PAIR_ENDINGS}?|двоек|двойк{GROUP_ENDINGS})\b',
                 '3': rf'\b(?:троек|тройк{GROUP_ENDINGS})\b',
                 '4': rf'\b(?:четверок|четверк{GROUP_ENDINGS})\b'}
# The group the question asks for: «найдите все тройки», «количество и наименьшую сумму пар», «среди четвёрок…».
# A pair may only be explained earlier in the condition («назовём неудачной парой…») while the question is about
# triples, and «ответом будет пара чисел» only describes the shape of the answer, so a bare mention is not enough.
ASKED = re.compile(r'(?:количество|найдите|найти|определите|определить|подсчитайте|посчитайте|среди)\s+(?:[\w-]+[\s,]+){0,4}?(?:'
                   + '|'.join(f'(?P<k{key}>{pattern})' for key, pattern in WORD_PATTERNS.items()) + ')')


def classify(text):
    """'2', '3' or '4' for the group the question asks about, '' for none."""
    asked = ASKED.search((text or '').lower().replace('ё', 'е'))
    return next(name[1:] for name, value in asked.groupdict().items() if value) if asked else ''


def subtopic_for(task, text):
    """The subtopic № 17 task should have now, or None when it must stay as it is (a teacher's own one)."""
    if task.subtopic and task.subtopic not in SUBTOPICS.values():
        return None
    return SUBTOPICS.get(classify(text), '')
