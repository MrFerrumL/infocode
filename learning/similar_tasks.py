"""The "Похожие" button: tasks of the same number with a similar condition.

Neighbours are computed offline by a multilingual sentence-embedding model from the source texts of the
KOMPEGE and K. Polyakov tasks (deploy/build_similar_tasks.py → learning/data/similar_tasks.json.gz) and
loaded with `manage.py sync_similar_tasks`. Tasks without a text (own and Yandex tasks) fall back to the
same number and difficulty.
"""
import gzip
import json
from pathlib import Path
from django.db import transaction
from . import game_groups
from .models import Practice, Task
from .similar_models import TaskSimilarity

DATA = Path(__file__).resolve().parent / 'data' / 'similar_tasks.json.gz'
SHOWN = 5


def key_of(task):
    return f'{task.source_system}:{task.topic.number}:{task.external_id}'


@transaction.atomic
def sync(path=DATA):
    data = json.loads(gzip.decompress(Path(path).read_bytes()))
    ids = {f'{s}:{n}:{e}': pk for pk, s, n, e in Task.objects.filter(external_id__isnull=False)
           .values_list('pk', 'source_system', 'topic__number', 'external_id')}
    TaskSimilarity.objects.all().delete()
    rows = []
    for key, neighbours in data['neighbours'].items():
        if key not in ids:
            continue
        found = [(ids[other], score) for other, score in neighbours if other in ids and ids[other] != ids[key]]
        rows += [TaskSimilarity(task_id=ids[key], other_id=pk, score=score, rank=rank) for rank, (pk, score) in enumerate(found, 1)]
    TaskSimilarity.objects.bulk_create(rows, batch_size=5000)
    return len({r.task_id for r in rows}), len(rows)


def similar_to(task, user, count=SHOWN):
    """(tasks, how) — the pupil's unsolved similar tasks first, then solved ones."""
    visible = Task.objects.filter(published=True, in_catalog=True).exclude(game_groups.grouped_follow_up())
    linked = list(TaskSimilarity.objects.filter(task=task, other__in=visible).values_list('other_id', flat=True))
    how = 'model'
    if not linked:
        how = 'number'
        numbers = game_groups.NUMBERS if task.topic.number in game_groups.NUMBERS and game_groups.active() else [task.topic.number]
        base = visible.filter(topic__number__in=numbers).exclude(pk=task.pk)
        linked = list(base.filter(difficulty=task.difficulty).order_by('?').values_list('pk', flat=True)[:count * 3]) or \
            list(base.order_by('?').values_list('pk', flat=True)[:count * 3])
    solved = set(Practice.objects.filter(user=user, correct=True, task_id__in=linked).values_list('task_id', flat=True))
    ordered = [pk for pk in linked if pk not in solved] + [pk for pk in linked if pk in solved]
    return ordered[:count], how
