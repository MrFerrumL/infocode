"""«Подборка по образцу»: КР, в каждом варианте которой стоит по задаче, похожей на заданный образец.

Образец — задача шаблона ДЗ (её условие) или показательная задача номера. Для каждого образца каждый вариант получает
ближайшую по смыслу задачу того же номера, только из разрешённых банков (например КЕГЭ без К. Полякова), без повторов
внутри КР и, пока хватает задач, между КР. Копия самого образца не берётся: нужны похожие, но не те же.
"""
import random
import numpy
from . import embeddings
from .exam_sources import OFFICIAL
from .kr_api import ComposeError, pool
from .models import TaskVector
from .similar_tasks import COPY

OFFICIAL_BONUS = 0.02   # of two equally close tasks the one from an official source wins
REUSE_PENALTY = 1.0     # a task already taken by another КР goes last
MIN_WORDS = 20          # a shorter condition (a picture without words) has no meaning to compare


def allowed_sources(sources, number):
    """Banks allowed for a number: {'default': ['kompege'], '17': ['kompege']} → the list for that number."""
    return sources.get(str(number)) or sources.get('default') or ['kompege']


def _vectors(queryset):
    """(task ids, matrix of their sentence vectors) for the tasks that have one."""
    rows = list(TaskVector.objects.filter(task__in=queryset, vector__isnull=False).values_list('task_id', 'vector'))
    if not rows:
        return [], numpy.zeros((0, embeddings.DIMENSIONS), dtype=numpy.float32)
    return [pk for pk, _ in rows], embeddings.matrix([bytes(v) for _, v in rows])


def auto_prototypes(number, count, allowed, banned=(), rng=None):
    """`count` tasks that stand for the number: official ones first, as unlike each other as possible (k-means++ sampling)."""
    rng = rng or random.SystemRandom()
    base = pool().filter(topic__number=number, source_system__in=allowed).exclude(pk__in=banned)
    ids, vectors = _vectors(base)
    if len(ids) < count:
        raise ComposeError(f'Для № {number} в разрешённых банках {len(ids)} задач с ответом, а нужно {count}.')
    official = dict(base.values_list('pk', 'exam_source'))
    preferred = [i for i, pk in enumerate(ids) if official[pk] in OFFICIAL]
    if len(preferred) < count:
        preferred = list(range(len(ids)))
    first = rng.choice(preferred)
    chosen, distance = [first], 1 - vectors @ vectors[first]
    while len(chosen) < count:
        weights = numpy.array([max(float(distance[i]), 0.0) ** 2 if i not in chosen else 0.0 for i in preferred])
        if weights.sum() <= 0:
            weights = numpy.array([0.0 if i in chosen else 1.0 for i in preferred])
        pick = preferred[int(rng.choices(range(len(preferred)), weights=weights.tolist())[0])]
        chosen.append(pick)
        distance = numpy.minimum(distance, 1 - vectors @ vectors[pick])
    return [ids[i] for i in chosen]


def _slot_vector(slot):
    """The vector of a prototype: of its task, or of its text; None when it has no words."""
    if slot.get('task'):
        row = TaskVector.objects.filter(task_id=slot['task'], vector__isnull=False).values_list('vector', flat=True).first()
        return embeddings.matrix([bytes(row)])[0] if row is not None else None
    text = embeddings.plain(slot.get('text') or '')
    return embeddings.embed([text])[0] if len(text) >= MIN_WORDS else None


def infer_number(vector, numbers, sources):
    """The number (among `numbers`) whose allowed tasks are on average closest to the vector."""
    best, best_score = None, -1.0
    for number in numbers:
        ids, vectors = _vectors(pool().filter(topic__number=number, source_system__in=allowed_sources(sources, number)))
        if ids:
            score = float(numpy.sort(vectors @ vector)[-3:].mean())
            if score > best_score:
                best, best_score = number, score
    return best


def compose_like(slots, variants, sources, used=(), rng=None, banned=()):
    """Variants (lists of task ids) with a task like each prototype in every variant, and a report.

    slots: [{'number': 17 | None, 'text': '…'} or {'number': 17, 'task': pk}]; a prototype without a number gets the one
    whose tasks it resembles most. `used` are tasks of other КР (taken last, only when nothing else is left), `banned` are
    not taken at all. Raises ComposeError when a number has too few tasks in the allowed banks."""
    rng = rng or random.SystemRandom()
    used = set(used)
    vectors_of = [_slot_vector(slot) for slot in slots]
    numbers = sorted({slot['number'] for slot in slots if slot.get('number')})
    slots = [dict(slot) for slot in slots]
    for slot, vector in zip(slots, vectors_of):
        if not slot.get('number'):
            slot['number'] = infer_number(vector, numbers, sources) if vector is not None else None
            if slot['number'] is None:
                raise ComposeError('Не удалось понять, к какому номеру относится задача-образец без номера.')
    result = [[] for _ in range(variants)]
    report = {'pairs': [], 'copies_skipped': 0, 'reused': 0, 'official': 0, 'chosen': 0}
    for number in sorted({slot['number'] for slot in slots}):
        mine = [k for k, slot in enumerate(slots) if slot['number'] == number]
        base = pool().filter(topic__number=number, source_system__in=allowed_sources(sources, number)).exclude(pk__in=banned)
        ids, matrix = _vectors(base)
        kind = dict(base.values_list('pk', 'exam_source'))
        if len(ids) < len(mine) * variants:
            raise ComposeError(f'Для № {number} в разрешённых банках {len(ids)} задач с ответом, а нужно {len(mine) * variants}.')
        known = [vectors_of[k] for k in mine if vectors_of[k] is not None]
        if known:
            centre = numpy.mean(known, axis=0)
        else:  # no prototype has words: the middle of the official tasks of the number
            centre = matrix[[i for i, pk in enumerate(ids) if kind[pk] in OFFICIAL] or list(range(len(ids)))].mean(axis=0)
        centre = centre / (numpy.linalg.norm(centre) or 1.0)
        prototypes = numpy.array([vectors_of[k] if vectors_of[k] is not None else centre for k in mine], dtype=numpy.float32)
        raw = matrix @ prototypes.T  # tasks × prototypes
        copies = set()
        for column, k in enumerate(mine):
            if slots[k].get('text') and not slots[k].get('task') and vectors_of[k] is not None:
                copies |= {i for i in numpy.nonzero(raw[:, column] > COPY)[0].tolist()}
        report['copies_skipped'] += len(copies)
        bonus = numpy.array([OFFICIAL_BONUS if kind[pk] in OFFICIAL else 0.0 for pk in ids], dtype=numpy.float32)
        reused = numpy.array([REUSE_PENALTY if pk in used else 0.0 for pk in ids], dtype=numpy.float32)
        score = raw + bonus[:, None] - reused[:, None]
        allowed = [i for i in range(len(ids)) if i not in copies]
        if len(allowed) < len(mine) * variants:  # the copies are needed after all: better a copy than a gap
            allowed = list(range(len(ids)))
        pairs = sorted(((float(score[i, column]), i, column) for i in allowed for column in range(len(mine))), reverse=True)
        taken, load, picked = set(), [0] * len(mine), [[] for _ in mine]
        for value, i, column in pairs:
            if i in taken or load[column] >= variants:
                continue
            taken.add(i)
            load[column] += 1
            picked[column].append(i)
            if len(taken) == len(mine) * variants:
                break
        for column, k in enumerate(mine):
            rng.shuffle(picked[column])  # the closest task does not always go to the first variant
            for v in range(variants):
                i = picked[column][v]
                result[v].append((number, k, ids[i]))
                report['pairs'].append(float(raw[i, column]))
                report['official'] += kind[ids[i]] in OFFICIAL
                report['reused'] += ids[i] in used
                report['chosen'] += 1
    return [[pk for _, _, pk in sorted(rows)] for rows in result], report
