"""Search by meaning: words or a photo of a condition → the closest catalogue tasks (learning/embeddings.py vectors).

The vectors of all tasks are kept in memory and reloaded only when tasks get new vectors, so a search costs one
model run for the query and one matrix product.
"""
import threading
from django.core.cache import cache
from django.db.models import Count, Max
from . import embeddings
from .similar_models import TaskVector

_lock = threading.Lock()
_index = {'stamp': None, 'ids': [], 'matrix': None}
MIN_WORDS = 3


def available():
    return embeddings.available()


def _vectors():
    """(task ids, matrix) of every task with a vector, refreshed when vectors change."""
    stamp = tuple(TaskVector.objects.filter(vector__isnull=False).aggregate(n=Count('task_id'), t=Max('updated_at')).values())
    with _lock:
        if _index['stamp'] != stamp:
            rows = list(TaskVector.objects.filter(vector__isnull=False).values_list('task_id', 'vector'))
            _index.update(stamp=stamp, ids=[pk for pk, _ in rows],
                          matrix=embeddings.matrix([bytes(v) for _, v in rows]) if rows else None)
        return _index['ids'], _index['matrix']


def rank(text, allowed, limit=60):
    """Ids of `allowed` tasks (a queryset of tasks) ordered by closeness to the text, best first;
    None when the text is too short to have a meaning (the caller then searches by words)."""
    import numpy
    text = embeddings.plain(text)
    if len(text.split()) < MIN_WORDS:
        return None
    ids, matrix = _vectors()
    if matrix is None:
        return []
    allowed_ids = set(allowed.values_list('pk', flat=True))
    keep = [i for i, pk in enumerate(ids) if pk in allowed_ids]
    if not keep:
        return []
    query = embeddings.embed([text])[0]
    scores = matrix[keep] @ query
    best = numpy.argsort(-scores)[:limit]
    return [ids[keep[i]] for i in best]


def allowed_to_read_photo(user, per_ten_minutes=30):
    """Photos cost CPU: a limit per person, teachers included."""
    key = f'photo-search:{user.pk}'
    count = cache.get(key, 0)
    if count >= per_ten_minutes:
        return False
    cache.set(key, count + 1, 600)
    return True
