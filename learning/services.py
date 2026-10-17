import random
import unicodedata
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import Task, Variant, VariantItem, ExamAnswer, ExamAttempt


def normalize(value):
    return ' '.join(unicodedata.normalize('NFKC', value).strip().replace('−', '-').split()).casefold()


def check_answer(value, expected):
    return bool(normalize(value)) and normalize(value) == normalize(expected)


# Primary (0–29) to test score, as on KOMPEGE.
EGE_SCALE = [0, 7, 14, 20, 27, 34, 40, 43, 46, 48, 51, 54, 56, 59, 62, 64, 67, 70, 72, 75, 78, 80, 83, 85, 88, 90, 93, 95, 98, 100]


def max_points(number):
    return 2 if number in (26, 27) else 1


def ege_points(number, value, expected):
    """KOMPEGE rules: 1 point for 1–25; for 26 and 27 two points, one for a partly right answer."""
    rows = [normalize(row).split() for row in value.splitlines() if normalize(row)]
    keys = [normalize(row).split() for row in expected.splitlines() if normalize(row)]
    if not rows:
        return 0
    if rows == keys:
        return max_points(number)
    if number not in (26, 27):
        return 0
    if len(keys) == 2 and number == 27:
        # Two files: one point if at least two of the four numbers are in place.
        hits = sum(1 for r in range(2) for c in range(2)
                   if r < len(rows) and c < len(rows[r]) and c < len(keys[r]) and rows[r][c] == keys[r][c])
        return 1 if hits >= 2 else 0
    got, key = (rows[0] + [''])[:2], (keys[0] + [''])[:2] if keys else ['', '']
    swapped = got[0] == key[1] and got[1] == key[0]
    return 1 if swapped or got[0] == key[0] or got[1] == key[1] else 0


@transaction.atomic
def create_variant(*, creator, title, topics, count=1, difficulty='', task_ids='', kind='exam', duration=235, group=None, published=False, code_policy=''):
    from .python_answers import CODE_MODES
    if code_policy and code_policy not in dict(CODE_MODES):
        raise ValidationError('Выберите допустимый режим отправки Python-кода.')
    topics = list(topics)
    if task_ids.strip():
        try:
            ids = list(dict.fromkeys(int(x) for x in task_ids.replace(',', ' ').split()))
        except ValueError:
            raise ValidationError('Укажите ID заданий через пробел или запятую.')
        if not ids or len(ids) > 100:
            raise ValidationError('Выберите от 1 до 100 заданий.')
        found = {t.id: t for t in Task.objects.filter(id__in=ids, published=True).select_related('topic').prefetch_related('content_blocks')}
        if len(found) != len(ids):
            raise ValidationError('Некоторые ID не найдены или задания не опубликованы.')
        selected = [found[i] for i in ids]
    else:
        if not topics:
            raise ValidationError('Выберите хотя бы один номер или укажите ID вручную.')
        selected, groups = [], []
        for topic in sorted(topics, key=lambda t: t.number):
            qs = Task.objects.filter(topic=topic, published=True, in_catalog=True).select_related('topic').prefetch_related('content_blocks')
            if difficulty:
                qs = qs.filter(difficulty=difficulty)
            pool = list(qs)
            if len(pool) < count:
                raise ValidationError(f'Для №{topic.number} доступно {len(pool)} заданий. Нужно {count}. Измените фильтр или добавьте задания.')
            # 20 and 21 follow the condition of the chosen 19 when the same group is available.
            partners = {(t.source_system, t.external_id): t for t in pool if t.external_id}
            if topic.number in (20, 21) and groups and all(key in partners for key in groups):
                picked = [partners[key] for key in groups]
            else:
                picked = random.SystemRandom().sample(pool, count)
            if topic.number == 19:
                groups = [(t.source_system, t.external_id) for t in picked if t.external_id and t.source_system]
                groups = groups if len(groups) == count else []
            selected.extend(picked)
    variant = Variant.objects.create(creator=creator, title=title, kind=kind, duration=duration, group=group, published=published)
    variant.topics.set({t.topic_id for t in selected})
    VariantItem.objects.bulk_create([VariantItem(variant=variant, task=t, position=i, number=t.topic.number, statement=t.statement, answer=t.answer, code_mode=code_policy or t.code_mode, solution=t.solution, attachment=t.attachment.name, blocks=[b.snapshot() for b in t.content_blocks.all() if not b.archived_at]) for i, t in enumerate(selected, 1)])
    return variant


def finish_attempt(attempt):
    if attempt.submitted_at:
        return
    score = 0
    ege = attempt.variant.scoring == 'ege'
    for item in attempt.variant.items.all():
        answer, _ = ExamAnswer.objects.get_or_create(attempt=attempt, item=item)
        code_ok = item.code_mode != 'required' or bool(answer.python_code.strip())
        if ege:
            answer.points = ege_points(item.number, answer.answer, item.answer) if code_ok else 0
            answer.correct = answer.points == max_points(item.number)
            score += answer.points
        else:
            answer.correct = check_answer(answer.answer, item.answer) and code_ok
            answer.points = int(answer.correct)
            score += int(answer.correct)
        answer.save(update_fields=['correct', 'points'])
    attempt.score = score
    if ege:
        attempt.primary_score = score
        attempt.test_score = EGE_SCALE[min(score, len(EGE_SCALE) - 1)]
    attempt.submitted_at = timezone.now()
    attempt.save(update_fields=['score', 'primary_score', 'test_score', 'submitted_at'])
    from .assignments import sync_exam_result
    sync_exam_result(attempt)


def expire_attempts(user=None):
    """Called on dashboards and by an optional scheduled management command."""
    qs = ExamAttempt.objects.filter(submitted_at=None, deadline__lte=timezone.now())
    if user is not None:
        qs = qs.filter(user=user)
    count = 0
    for pk in list(qs.values_list('pk', flat=True)):
        with transaction.atomic():
            attempt = ExamAttempt.objects.select_for_update().get(pk=pk)
            if not attempt.submitted_at:
                finish_attempt(attempt)
                count += 1
    return count
