"""The "Похожие" button: tasks of the same number with a similar condition.

First choice: the closest tasks by meaning, from sentence vectors the server makes itself for every catalogue
task (learning/embeddings.py, `manage.py embed_tasks`). Without vectors: the links computed offline for KOMPEGE and
K. Polyakov (deploy/build_similar_tasks.py → `manage.py sync_similar_tasks`); without either: the same number and
difficulty.
"""
import gzip
import json
import subprocess
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


def own_vector(task):
    """The task's vector; a task without one (a picture-only KIM task, a new one) gets it now from its words or picture."""
    row = TaskVector.objects.filter(task=task).first()
    if row and row.vector is not None:
        return bytes(row.vector)
    if not embeddings.available():
        return None
    text = embeddings.task_text(task, row.source_text if row else '')
    if not text:
        from . import ocr
        block = task.content_blocks.filter(purpose='statement', kind='image').exclude(image='').order_by('position', 'id').first()
        if block and ocr.available():
            try:
                with block.image.open('rb') as picture:
                    words = embeddings.plain(ocr.read(picture.read(), timeout=15))
            except (OSError, ocr.Unreadable, ValueError, subprocess.SubprocessError):
                words = ''
            if words:
                TaskVector.objects.update_or_create(task=task, defaults={'source_text': words})
                text = embeddings.task_text(task, words)
    if not text:
        return None
    vector = embeddings.to_bytes(embeddings.embed([text])[0])
    TaskVector.objects.update_or_create(task=task, defaults={'digest': embeddings.digest(text), 'vector': vector})
    return vector


def _features(pks):
    """{task id: (has a data file, answer parts, subtopic, own level)} — what a similar task should share."""
    from .models import TaskBlock
    with_files = set(TaskBlock.objects.filter(task_id__in=pks, purpose='statement', kind='file').values_list('task_id', flat=True))
    rows = Task.all_objects.filter(pk__in=pks).values_list('pk', 'attachment', 'answer', 'subtopic', 'difficulty', 'original_difficulty')
    return {pk: (bool(file) or pk in with_files, min(len(answer.split()), 3), subtopic.strip().lower(), original or level)
            for pk, file, answer, subtopic, level, original in rows}


def by_vector(task, visible, numbers, count):
    """The closest tasks by meaning of the condition (vectors made on this server by learning/embeddings.py).

    The model's closeness comes first; then a similar task has to be of the same kind: with a data file when the task
    has one (and without when it has none), the same shape of answer (one number, two…). The same subtopic and level
    lift a task a little; the chosen ones are not near-copies of each other, nor of the task itself."""
    own = own_vector(task)
    if own is None:
        return []
    rows = list(TaskVector.objects.filter(task__in=visible.filter(topic__number__in=numbers).exclude(pk=task.pk), vector__isnull=False)
                .values_list('task_id', 'vector'))
    if not rows:
        return []
    import numpy
    vectors = embeddings.matrix([bytes(v) for _, v in rows])
    scores = vectors @ embeddings.matrix([own])[0]
    top = [int(i) for i in numpy.argsort(-scores)[:count * 4 + 30]]
    ids = [rows[i][0] for i in top]
    # The same task copied into another bank is not «похожая»: an almost equal text with the same answer is skipped.
    answer = ' '.join(task.answer.split())
    copies = {pk for pk, a in Task.all_objects.filter(pk__in=[rows[i][0] for i in top if scores[i] > COPY]).values_list('pk', 'answer')
              if ' '.join(a.split()) == answer}
    features = _features(ids + [task.pk])
    mine = features.get(task.pk)
    if mine is None:
        return []
    kind = [i for i in top if rows[i][0] not in copies and features.get(rows[i][0], (None, None))[:2] == mine[:2]]
    candidates = kind if len(kind) >= count else kind + [i for i in top if i not in kind and rows[i][0] not in copies]
    bonus = lambda i: (0.02 if mine[2] and features[rows[i][0]][2] == mine[2] else 0) + (0.01 if features[rows[i][0]][3] == mine[3] else 0)  # noqa: E731
    candidates.sort(key=lambda i: -(scores[i] + bonus(i)))
    chosen = []
    for i in candidates:
        if all(float(vectors[i] @ vectors[j]) <= COPY for j in chosen):
            chosen.append(i)
        if len(chosen) == count:
            break
    return [rows[i][0] for i in chosen]


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
