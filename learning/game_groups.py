"""Numbers 19–21 share one condition; imported groups are three tasks with one source ID.

In the catalogue the group is a single card: task 19 with the questions of 20 and 21
attached below it. Tasks without a 19 partner (e.g. written by a teacher) stay separate.
"""
from django.db.models import Exists, OuterRef, Prefetch, Q

from .models import Practice, Task, TaskBlock
from .subjects import current

FIRST = 19
FOLLOW_UPS = (20, 21)
NUMBERS = (FIRST,) + FOLLOW_UPS


def partner_19():
    return Task.objects.filter(published=True, in_catalog=True, topic__number=FIRST, source_system=OuterRef('source_system'),
                               external_id=OuterRef('external_id')).exclude(source_system='')


def active():
    """Only informatics has 19–21 game groups; in physics 19, 20 and 21 are separate numbers."""
    return current() == 'inf'


def grouped_follow_up():
    """Q for 20/21 tasks that are shown inside their 19 card."""
    if not active():
        return Q(pk__in=[])
    return Q(topic__number__in=FOLLOW_UPS, external_id__isnull=False) & Q(Exists(partner_19()))


def fold_follow_ups(qs):
    """Replace grouped 20/21 tasks in a filtered queryset by their 19 card."""
    hidden = qs.filter(grouped_follow_up())
    keys = set(hidden.values_list('source_system', 'external_id'))
    ids = set(qs.exclude(grouped_follow_up()).values_list('id', flat=True))
    if keys:
        partners = Task.objects.filter(published=True, in_catalog=True, topic__number=FIRST, external_id__in={k[1] for k in keys})
        ids |= {pk for pk, system, ident in partners.values_list('id', 'source_system', 'external_id') if (system, ident) in keys}
    return ids


def attach_follow_ups(tasks, user):
    """Set task.follow_ups for 19 cards; their statement blocks exclude the shared condition."""
    if not active():
        return {}
    firsts = [t for t in tasks if t.topic.number == FIRST and t.external_id and t.source_system]
    if not firsts:
        return {}
    rows = Task.objects.filter(published=True, in_catalog=True, topic__number__in=FOLLOW_UPS, external_id__in=[t.external_id for t in firsts]).select_related('topic').prefetch_related(
        Prefetch('content_blocks', queryset=TaskBlock.objects.all(), to_attr='catalog_blocks'),
        Prefetch('practice_set', queryset=Practice.objects.filter(user=user).order_by('-created_at', '-pk')[:5], to_attr='catalog_history')).order_by('topic__number')
    by_key = {}
    for row in rows:
        by_key.setdefault((row.source_system, row.external_id), []).append(row)
    attached = {}
    for task in firsts:
        shared = {b.image.name for b in task.catalog_blocks if b.kind == 'image' and b.image}
        task.follow_ups = by_key.get((task.source_system, task.external_id), [])
        for item in task.follow_ups:
            item.catalog_blocks = [b for b in item.catalog_blocks if not (b.kind == 'image' and b.image and b.image.name in shared)]
            attached[item.pk] = item
    return attached
