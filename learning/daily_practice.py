"""«Тренировка дня»: a short personal set of tasks, new every day.

Picked from what the pupil did before today, so the set stays the same all day:
1. tasks similar to recent mistakes (neural neighbours, learning/similar_tasks.py);
2. tasks from the weakest numbers (lowest share of right answers);
3. an easy task from a number not tried yet;
4. anything unsolved to fill up.
Already solved tasks are never offered again; 20/21 of a 19–21 group come inside their 19 card.
"""
import random
from datetime import datetime, time, timedelta
from django.utils import timezone
from . import game_groups
from .models import Practice, Task
from .similar_models import TaskSimilarity
from .subjects import current as current_subject

SIZE = 8


def number_of(n):
    return 19 if n in game_groups.FOLLOW_UPS and game_groups.active() else n


def label(n):
    return '19–21' if n == 19 and game_groups.active() else str(n)


def daily_tasks(user, day=None, size=SIZE):
    """[(task_id, reason)] for the pupil's set of the day."""
    day = day or timezone.localdate()
    start = timezone.make_aware(datetime.combine(day, time.min))
    rng = random.Random(f'{user.pk}:{day.isoformat()}')
    before = Practice.objects.filter(user=user, created_at__lt=start, task__topic__subject=current_subject())
    solved = set(before.filter(correct=True).values_list('task_id', flat=True))
    latest, attempts = {}, {}
    for task_id, correct, number, at in before.order_by('-created_at', '-pk').values_list('task_id', 'correct', 'task__topic__number', 'created_at'):
        latest.setdefault(task_id, correct)
        if at >= start - timedelta(days=90):
            stats = attempts.setdefault(number_of(number), [0, 0])
            stats[0] += 1
            stats[1] += correct
    pool = {}
    for pk, number, level in Task.objects.filter(published=True, in_catalog=True).exclude(game_groups.grouped_follow_up()).values_list('pk', 'topic__number', 'difficulty'):
        if pk not in solved:
            pool.setdefault(number_of(number), []).append((pk, level))
    visible = {pk for items in pool.values() for pk, _ in items}
    picked, taken = [], set()

    def take(pk, reason):
        if pk in visible and pk not in taken and len(picked) < size:
            picked.append((pk, reason))
            taken.add(pk)
            return True
        return False

    # 1. Similar to the latest mistakes (a later right answer clears a mistake).
    mistakes = [pk for pk, correct in latest.items() if not correct][:12]
    numbers = dict(Task.objects.filter(pk__in=mistakes).values_list('pk', 'topic__number'))
    for mistake in mistakes:
        if len(picked) >= size // 2:
            break
        for other in TaskSimilarity.objects.filter(task_id=mistake).order_by('rank').values_list('other_id', flat=True):
            if take(other, f'Похожа на задачу № {label(number_of(numbers.get(mistake, 0)))}, где был неверный ответ'):
                break

    # 2. Weakest numbers: at least 3 answers in 90 days, lowest share right first.
    weak = sorted(((right / total, n) for n, (total, right) in attempts.items() if total >= 3 and right / total < .8))
    for share, n in weak[:3]:
        items = pool.get(n, [])
        easier = [pk for pk, level in items if level in ('easy', 'medium')] if share < .5 else []
        for pk in rng.sample(easier or [pk for pk, _ in items], min(2, len(easier or items))):
            take(pk, f'Слабое место: № {label(n)}, {round(share * 100)}% верных ответов')

    # 3. A number not tried yet, starting easy.
    untried = [n for n in sorted(pool) if n not in attempts]
    rng.shuffle(untried)
    for n in untried[:2 if attempts else size]:
        easy = [pk for pk, level in pool[n] if level == 'easy'] or [pk for pk, _ in pool[n]]
        take(rng.choice(easy), f'Новый номер: № {label(n)}')

    # 4. Fill up with variety: at most two tasks per number from the ones in work, then more new numbers.
    number_by_task = {pk: n for n, items in pool.items() for pk, _ in items}
    per = lambda n: sum(1 for pk, _ in picked if number_by_task.get(pk) == n)
    tried = [n for n in sorted(pool) if n in attempts]
    rng.shuffle(tried)
    for n in tried:
        spare = [pk for pk, _ in pool[n] if pk not in taken]
        while per(n) < 2 and spare and len(picked) < size:
            take(spare.pop(rng.randrange(len(spare))), f'Повторение: № {label(n)}')
    for n in untried:
        if len(picked) >= size:
            break
        if per(n) == 0:
            easy = [pk for pk, level in pool[n] if level == 'easy'] or [pk for pk, _ in pool[n]]
            take(rng.choice(easy), f'Новый номер: № {label(n)}')
    spare = [pk for pk in visible if pk not in taken]
    rng.shuffle(spare)
    while len(picked) < size and spare:
        pk = spare.pop()
        take(pk, f'Повторение: № {label(number_by_task[pk])}')
    return picked
