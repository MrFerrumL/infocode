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
        selected = []
        for topic in topics:
            qs = Task.objects.filter(topic=topic, published=True).select_related('topic').prefetch_related('content_blocks')
            if difficulty:
                qs = qs.filter(difficulty=difficulty)
            pool = list(qs)
            if len(pool) < count:
                raise ValidationError(f'Для №{topic.number} доступно {len(pool)} заданий. Нужно {count}. Измените фильтр или добавьте задания.')
            selected.extend(random.SystemRandom().sample(pool, count))
    variant = Variant.objects.create(creator=creator, title=title, kind=kind, duration=duration, group=group, published=published)
    variant.topics.set({t.topic_id for t in selected})
    VariantItem.objects.bulk_create([VariantItem(variant=variant, task=t, position=i, number=t.topic.number, statement=t.statement, answer=t.answer, code_mode=code_policy or t.code_mode, solution=t.solution, attachment=t.attachment.name, blocks=[b.snapshot() for b in t.content_blocks.all() if not b.archived_at]) for i, t in enumerate(selected, 1)])
    return variant


def finish_attempt(attempt):
    if attempt.submitted_at:
        return
    score = 0
    for item in attempt.variant.items.all():
        answer, _ = ExamAnswer.objects.get_or_create(attempt=attempt, item=item)
        answer.correct = check_answer(answer.answer, item.answer) and (item.code_mode != 'required' or bool(answer.python_code.strip()))
        answer.save(update_fields=['correct'])
        score += int(answer.correct)
    attempt.score = score
    attempt.submitted_at = timezone.now()
    attempt.save(update_fields=['score', 'submitted_at'])
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
