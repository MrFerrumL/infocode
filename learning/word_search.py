"""Search of tasks by words: every word of the query must be somewhere in the task, in any order and in any form.

Where a word is looked for: the title, tags, subtopic, the text of the condition and, for a task whose condition is a
picture, the words kept with it (TaskVector.source_text: all tasks of the catalogue have them). A word is a stem:
«восьмерич» finds «восьмеричной»; «е» and «ё» are the same letter; capitals do not matter. With the number of the task
chosen in the filter, «пар сумма остаток» finds the tasks of that number that mention all three words.
"""
import re
from django.db.models import Exists, OuterRef, Q
from .models import TaskBlock, TaskVector

MAX_WORDS = 8


def words_of(query):
    """The distinct words of a query (at most MAX_WORDS), without the punctuation around them."""
    found = (word.strip('.,;:!?()[]{}«»"\'') for word in (query or '').replace(' ', ' ').split())
    distinct = {}
    for word in found:
        if word:
            distinct.setdefault(word.lower(), word)  # «Сумма» and «сумма» are one word
    return list(distinct.values())[:MAX_WORDS]


CYRILLIC = re.compile(r'[а-яё]+')
ENDINGS = 'аеёиоуыэюяйь'


def stem(word):
    """A Russian word without up to two ending letters («суммы» and «сумма» both become «сумм»), so that any form is found.
    Short words, and words with digits or Latin letters («F(n)», «mod»), stay as they are."""
    word = word.lower()
    if not CYRILLIC.fullmatch(word):
        return word
    for _ in range(2):
        if len(word) > 4 and word[-1] in ENDINGS:
            word = word[:-1]
    return word


def pattern(word):
    """A case-insensitive regular expression for the word's stem where «е» and «ё» match each other."""
    return ''.join('[её]' if letter in 'её' else letter for letter in re.escape(stem(word)))


def has_word(word):
    """Q for the tasks that have the word in any of the places."""
    rx = pattern(word)
    return (Q(title__iregex=rx) | Q(tags__iregex=rx) | Q(subtopic__iregex=rx) | Q(statement__iregex=rx)
            | Q(Exists(TaskBlock.all_objects.filter(task=OuterRef('pk'), purpose='statement', archived_at__isnull=True, text__iregex=rx)))
            | Q(Exists(TaskVector.objects.filter(task=OuterRef('pk'), source_text__iregex=rx))))


def search(tasks, query):
    """The tasks that have every word of the query; one number also finds the task with that ID (here or in its source bank)."""
    words = words_of(query)
    if not words:
        return tasks
    if len(words) == 1 and words[0].isdigit():
        return tasks.filter(has_word(words[0]) | Q(pk=int(words[0])) | Q(external_id=int(words[0])))
    for word in words:
        tasks = tasks.filter(has_word(word))
    return tasks
