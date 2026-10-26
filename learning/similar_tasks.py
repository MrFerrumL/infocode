"""The "Похожие" button: tasks of the same number with a similar condition.

First choice: the closest tasks by meaning, from sentence vectors the server makes itself for every catalogue
task (learning/embeddings.py, `manage.py embed_tasks`). Without vectors: the links computed offline for KOMPEGE and
K. Polyakov (deploy/build_similar_tasks.py → `manage.py sync_similar_tasks`); without either: the same number and
difficulty.
"""
import gzip
import json
from pathlib import Path
from django.db import transaction
from . import embeddings, game_groups
from .models import Practice, Task
from .similar_models import TaskSimilarity, TaskVector

DATA = Path(__file__).resolve().parent / 'data' / 'similar_tasks.json.gz'
SHOWN = 5
COPY = 0.985  # cosine above which two conditions are the same text


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


def by_vector(task, visible, numbers, count):
    """The closest tasks by meaning of the condition (vectors made on this server by learning/embeddings.py)."""
    own = TaskVector.objects.filter(task=task, vector__isnull=False).values_list('vector', flat=True).first()
    if own is None:
        return []
    rows = list(TaskVector.objects.filter(task__in=visible.filter(topic__number__in=numbers).exclude(pk=task.pk), vector__isnull=False)
                .values_list('task_id', 'vector'))
    if not rows:
        return []
    import numpy
    scores = embeddings.matrix([bytes(v) for _, v in rows]) @ embeddings.matrix([bytes(own)])[0]
    best = [rows[i][0] for i in numpy.argsort(-scores)[:count + 10]]
    # The same task copied into another bank is not «похожая»: an almost equal text with the same answer is skipped.
    copies = {i for i, s in zip((r[0] for r in rows), scores) if s > COPY}
    if copies:
        answer = ' '.join(task.answer.split())
        copies = {pk for pk, a in Task.all_objects.filter(pk__in=copies & set(best)).values_list('pk', 'answer') if ' '.join(a.split()) == answer}
    return [pk for pk in best if pk not in copies][:count]


def similar_to(task, user, count=SHOWN):
    """(tasks, how) — the pupil's unsolved similar tasks first, then solved ones."""
    visible = Task.objects.filter(published=True, in_catalog=True).exclude(game_groups.grouped_follow_up())
    numbers = game_groups.NUMBERS if task.topic.number in game_groups.NUMBERS and game_groups.active() else [task.topic.number]
    linked = by_vector(task, visible, numbers, count * 3)
    how = 'model'
    if not linked:
        linked = list(TaskSimilarity.objects.filter(task=task, other__in=visible).values_list('other_id', flat=True))
    if not linked:
        how = 'number'
        base = visible.filter(topic__number__in=numbers).exclude(pk=task.pk)
        linked = list(base.filter(difficulty=task.difficulty).order_by('?').values_list('pk', flat=True)[:count * 3]) or \
            list(base.order_by('?').values_list('pk', flat=True)[:count * 3])
    solved = set(Practice.objects.filter(user=user, correct=True, task_id__in=linked).values_list('task_id', flat=True))
    ordered = [pk for pk in linked if pk not in solved] + [pk for pk in linked if pk in solved]
    return ordered[:count], how
