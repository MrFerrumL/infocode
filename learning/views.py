import csv
import math
import re
from datetime import timedelta
from pathlib import Path
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, Count, F, IntegerField, Prefetch, Q, Value, When
from django.http import FileResponse, Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST
from .forms import RegisterForm, AnswerForm, VariantForm, TaskForm
from .models import Topic, Task, Practice, Bookmark, Variant, ExamAttempt, ExamAnswer, Section, Lesson, LessonProgress, LessonFile, VariantItem, TaskBlock, Course, Step, StudentAssignment
from .security import form_token, claim_submission
from .services import check_answer, create_variant, finish_attempt, expire_attempts
from . import game_groups
from .sources import KINDS, SITES

staff_required = user_passes_test(lambda u: u.is_active and u.is_staff)


def visible_variants(user):
    qs = Variant.objects.all()
    if not user.is_staff:
        qs = qs.filter(published=True).filter(Q(assigned_only=False) & (Q(group__isnull=True) | Q(group__students=user)) | Q(assigned_only=True, assignment__recipients__student=user))
    elif not user.is_superuser:
        qs = qs.filter(Q(assigned_only=False) | Q(creator=user))
    return qs.distinct()


def register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = RegisterForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user)
        from django.utils.http import url_has_allowed_host_and_scheme
        target = request.GET.get('next', '')
        return redirect(target if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}) else 'dashboard')
    return render(request, 'registration/register.html', {'form': form, 'next': request.GET.get('next', '')})


def topic_stats(user):
    solved = set(Practice.objects.filter(user=user, correct=True).values_list('task_id', flat=True))
    # Aggregation does not apply Meta.ordering; PostgreSQL otherwise returns arbitrary topic order.
    topics = list(Topic.objects.annotate(total=Count('tasks', filter=Q(tasks__published=True, tasks__in_catalog=True, tasks__archived_at=None))).order_by('number'))
    task_map = dict(Task.objects.filter(published=True, in_catalog=True).values_list('id', 'topic_id'))
    for topic in topics:
        topic.solved = sum(1 for pk in solved if task_map.get(pk) == topic.id)
        topic.percent = round(topic.solved / topic.total * 100) if topic.total else 0
    return topics, solved


@login_required
def dashboard(request):
    expire_attempts(request.user)
    topics, solved = topic_stats(request.user)
    attempts = Practice.objects.filter(user=request.user)
    total = attempts.count()
    accuracy = round(attempts.filter(correct=True).count() / total * 100) if total else 0
    from .course_services import available_courses, current_progress
    completed_steps = set(current_progress(request.user).values_list('step_id', flat=True))
    available_lessons = Lesson.objects.filter(published=True, section__course__in=available_courses(request.user)).prefetch_related('steps')
    done = [lesson.pk for lesson in available_lessons if (ids := {s.pk for s in lesson.steps.all() if s.published}) and ids.issubset(completed_steps)]
    next_lesson = available_lessons.exclude(id__in=done).first()
    return render(request, 'dashboard.html', {'topics': topics, 'solved_count': len(solved), 'accuracy': accuracy, 'total': total, 'completed_lessons': len(done), 'next_lesson': next_lesson, 'recent': ExamAttempt.objects.filter(user=request.user, submitted_at__isnull=False).select_related('variant')[:4], 'nav': 'dashboard'})


CATALOG_SORTS = [('', 'По умолчанию'), ('asc', 'По номеру: с первой задачи'), ('desc', 'По номеру: с последней'),
                 ('easy', 'Сначала простые'), ('hard', 'Сначала сложные')]


@login_required
def catalog(request):
    submitted, answer_form, result = None, None, None
    if request.method == 'POST':
        if not request.POST.get('submit_token'):
            raise PermissionDenied('Обновите страницу перед отправкой ответа.')
        submitted = get_object_or_404(Task.objects.select_related('topic'), pk=request.POST.get('task_id') if request.POST.get('task_id', '').isdigit() else 0, published=True, in_catalog=True)
        answer_form = AnswerForm(request.POST, mode=submitted.code_mode, prefix=f'task-{submitted.pk}')
        if answer_form.is_valid():
            with transaction.atomic():
                if claim_submission(request, f'task:{submitted.pk}'):
                    result = Practice.objects.create(user=request.user, task=submitted,
                        answer=answer_form.cleaned_data['answer'], python_code=answer_form.cleaned_data.get('python_code', ''),
                        correct=check_answer(answer_form.cleaned_data['answer'], submitted.answer))
            # A retry with the same token never creates a second attempt.
            if result is None:
                result = Practice.objects.filter(user=request.user, task=submitted).first()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            history = list(Practice.objects.filter(user=request.user, task=submitted)[:5])
            return JsonResponse({'feedback': render_to_string('partials/catalog_feedback.html', {
                'task': submitted, 'result': result, 'history': history,
                'can_reveal': request.user.is_staff or bool(history)}, request=request),
                'errors': {key: list(value) for key, value in answer_form.errors.items()},
                'submit_token': form_token(request.user, f'task:{submitted.pk}'),
                'solved': Practice.objects.filter(user=request.user, task=submitted, correct=True).exists()})
        if answer_form.is_valid():
            # Progressive fallback stays in the catalogue and keeps its filters and page.
            return redirect(request.get_full_path() + f'#task-{submitted.pk}')
    topics, solved = topic_stats(request.user)
    qs = Task.objects.filter(published=True, in_catalog=True)
    number = request.GET.get('number', '')
    if number in ('20', '21'):
        number = '19'
    if number == '19':
        qs = qs.filter(topic__number__in=game_groups.NUMBERS)
    elif number.isdigit():
        qs = qs.filter(topic__number=int(number))
    difficulty = request.GET.get('difficulty', '')
    if difficulty:
        qs = qs.filter(difficulty=difficulty)
    # As on KOMPEGE: what the task is (demo, real exam, probe, author's) and which site it comes from.
    kind = request.GET.get('kind', '')
    if kind in dict(KINDS):
        qs = qs.filter(source_kind=kind)
    site = request.GET.get('site', '')
    if site in SITES and site:
        qs = qs.filter(source_system=site)
    elif site == 'own':
        qs = qs.filter(source_system='')
    query = request.GET.get('q', '').strip()
    if query:
        qs = qs.filter(Q(title__icontains=query) | Q(tags__icontains=query) | Q(subtopic__icontains=query) | Q(statement__icontains=query) | Q(content_blocks__text__icontains=query, content_blocks__purpose='statement') | ((Q(id=int(query)) | Q(external_id=int(query))) if query.isdigit() else Q(pk__isnull=True))).distinct()
    state = request.GET.get('state', '')
    saved = set(Bookmark.objects.filter(user=request.user).values_list('task_id', flat=True))
    if state == 'saved':
        qs = qs.filter(id__in=saved)
    elif state == 'mistakes':
        # Latest practice answer must be wrong; a later correct answer clears the mistake.
        latest = {}
        for task_id, correct in Practice.objects.filter(user=request.user).values_list('task_id', 'correct'):
            latest.setdefault(task_id, correct)
        qs = qs.filter(id__in=[pk for pk, correct in latest.items() if not correct])
    elif state == 'new':
        qs = qs.exclude(id__in=solved)
    # A 19–21 group is one card: grouped 20/21 tasks are answered inside their 19 card.
    # By default each bank keeps its own order: KOMPEGE newest first (demo, retake, main wave…),
    # K. Polyakov from his first task (17.1, 17.2…), own tasks last. 'asc'/'desc' sort by source number.
    sort = {'old': 'asc'}.get(request.GET.get('sort', ''), request.GET.get('sort', ''))
    rank = Case(*[When(difficulty=value, then=Value(i)) for i, (value, _) in enumerate(Task.LEVELS)], output_field=IntegerField())
    bank = Case(When(source_system='kompege', then=Value(0)), When(source_system='polyakov', then=Value(1)), default=Value(2), output_field=IntegerField())
    polyakov_first = Case(When(source_system='polyakov', then=F('external_id')), default=Value(0), output_field=IntegerField())
    order = {'asc': [bank, F('external_id').asc(nulls_last=True), 'id'],
             'desc': [bank, F('external_id').desc(nulls_last=True), '-id'],
             'easy': [rank, bank, polyakov_first, F('external_id').desc(nulls_last=True), '-id'],
             'hard': [rank.desc(), bank, polyakov_first, F('external_id').desc(nulls_last=True), '-id']}.get(
        sort, [bank, polyakov_first, F('external_id').desc(nulls_last=True), '-id'])
    qs = Task.objects.filter(id__in=game_groups.fold_follow_ups(qs)).order_by('topic__number', *order).select_related('topic').prefetch_related(
        Prefetch('content_blocks', queryset=TaskBlock.objects.all(), to_attr='catalog_blocks'),
        Prefetch('practice_set', queryset=Practice.objects.filter(user=request.user).order_by('-created_at', '-pk')[:5], to_attr='catalog_history'))
    page = Paginator(qs, 12).get_page(request.GET.get('page'))
    game_groups.attach_follow_ups(page.object_list, request.user)
    for task in [t for card in page for t in [card, *getattr(card, 'follow_ups', [])]]:
        task.solved = task.id in solved
        task.saved = task.id in saved
        task.answer_form = answer_form if submitted and submitted.pk == task.pk else AnswerForm(
            mode=task.code_mode, prefix=f'task-{task.pk}',
            initial={'python_code': task.catalog_history[0].python_code if task.catalog_history else ''})
        task.submit_token = form_token(request.user, f'task:{task.pk}')
        task.can_reveal = request.user.is_staff or bool(task.catalog_history)
    for topic in topics:
        topic.chip = '19–21' if topic.number == game_groups.FIRST else str(topic.number)
        topic.folded = topic.number in game_groups.FOLLOW_UPS
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'catalog.html', {'levels': Task.LEVELS, 'topics': topics, 'page': page, 'number': number, 'difficulty': difficulty, 'query': query, 'state': state, 'sort': sort, 'sorts': CATALOG_SORTS, 'kind': kind, 'kinds': KINDS, 'site': site,
        'sites': [(key or 'own', name) for key, name in SITES.items() if Task.objects.filter(published=True, in_catalog=True, source_system=key).exists()], 'params': params.urlencode(), 'nav': 'catalog'})


@login_required
@transaction.atomic
def task_detail(request, pk):
    tasks = Task.objects.select_related('topic')
    if not request.user.is_staff:
        tasks = tasks.filter(published=True, in_catalog=True)
    task = get_object_or_404(tasks, pk=pk)
    latest = Practice.objects.filter(user=request.user, task=task).first() if task.code_mode != 'off' else None
    form = AnswerForm(request.POST or None, mode=task.code_mode, initial={'python_code': latest.python_code if latest else ''})
    result = None
    if request.method == 'POST' and form.is_valid() and claim_submission(request, f'task:{pk}'):
        result = Practice.objects.create(user=request.user, task=task, answer=form.cleaned_data['answer'], python_code=form.cleaned_data.get('python_code', ''), correct=check_answer(form.cleaned_data['answer'], task.answer))
    history = Practice.objects.filter(user=request.user, task=task)[:5]
    can_reveal = request.user.is_staff or result is not None or history.exists()
    return render(request, 'task.html', {'submit_token': form_token(request.user, f'task:{pk}'), 'task': task, 'form': form, 'result': result, 'history': history, 'can_reveal': can_reveal, 'saved': Bookmark.objects.filter(user=request.user, task=task).exists(), 'nav': 'catalog'})


@login_required
@require_POST
def bookmark(request, pk):
    task = get_object_or_404(Task, pk=pk, published=True)
    obj, created = Bookmark.objects.get_or_create(user=request.user, task=task)
    if not created:
        obj.delete()
    return redirect('task', pk=pk)


def posted_answer(request, item):
    """KEGE answer grid: cells joined by spaces, rows by newlines; plain field otherwise."""
    if f'cell_{item.pk}_0_0' not in request.POST:
        return request.POST.get(f'answer_{item.pk}', '').strip()[:250]
    rows = [' '.join(v for v in (request.POST.get(f'cell_{item.pk}_{r}_{c}', '').strip() for c in range(item.answer_cols)) if v)
            for r in range(item.answer_rows)]
    while rows and not rows[-1]:
        rows.pop()
    return '\n'.join(rows)[:250]


def answer_grid(item, value):
    rows = value.split('\n') if value else []
    return [[(rows[r].split() + [''] * item.answer_cols)[c] if r < len(rows) else '' for c in range(item.answer_cols)] for r in range(item.answer_rows)]


@login_required
def variants(request):
    kind = 'test' if request.path.startswith('/tests') else 'exam'
    qs = visible_variants(request.user).filter(kind=kind).annotate(task_count=Count('items')).prefetch_related('topics')
    imported = [v for v in qs if v.source_system == 'kompege']
    own = [v for v in qs if v.source_system != 'kompege']
    # KOMPEGE archive layout: school year (newest first) → collection → variants in source order.
    best = {}
    for variant_id, score in ExamAttempt.objects.filter(user=request.user, variant__in=imported, submitted_at__isnull=False).values_list('variant_id', 'test_score'):
        best[variant_id] = max(best.get(variant_id, 0), score or 0)
    years = {}
    for v in sorted(imported, key=lambda v: (v.school_year, -v.sort_order), reverse=True):
        v.best_score = best.get(v.pk)
        # Button label as in the source archive; the heading above already names year and collection.
        v.short = v.title.split(' · ', 1)[-1]
        if v.collection.startswith('Варианты от '):
            v.short = v.short.removesuffix(' от ' + v.collection[len('Варианты от '):])
        collections = years.setdefault(v.school_year, {})
        collections.setdefault(v.collection, []).append(v)
    archive = [{'school_year': year, 'exam_year': int(year[:4]) + 1, 'collections': [{'title': t, 'variants': vs} for t, vs in cols.items()]}
               for year, cols in years.items()]
    return render(request, 'variants.html', {'variants': own, 'archive': archive, 'kind': kind, 'nav': 'tests' if kind == 'test' else 'variants'})


@login_required
def variant_detail(request, pk):
    expire_attempts(request.user)
    variant = get_object_or_404(visible_variants(request.user), pk=pk)
    return render(request, 'variant.html', {'variant': variant, 'attempts': ExamAttempt.objects.filter(user=request.user, variant=variant), 'start_token': form_token(request.user, f'start-exam:{pk}'), 'nav': 'variants'})


@login_required
@require_POST
@transaction.atomic
def start_exam(request, pk):
    variant = get_object_or_404(visible_variants(request.user), pk=pk)
    if not variant.items.exists():
        messages.error(request, 'В работе пока нет заданий.')
        return redirect('variant', pk=pk)
    from .models import AssignmentRecipient
    from .assignments import assignment_block
    expire_attempts(request.user)
    deadline = timezone.now() + timedelta(minutes=variant.duration)
    if variant.assigned_only:
        recipient = get_object_or_404(AssignmentRecipient.objects.select_for_update().select_related('assignment'), assignment__variant=variant, student=request.user)
        blocked = assignment_block(recipient)
        if blocked:
            messages.error(request, blocked)
            return redirect('assignment_detail', pk=recipient.assignment_id)
        if not recipient.assignment.allow_late:
            deadline = min(deadline, recipient.assignment.due_at)
    active = ExamAttempt.objects.filter(user=request.user, variant=variant, submitted_at=None).first()
    if active:
        return redirect('exam', pk=active.pk)
    if not claim_submission(request, f'start-exam:{pk}'):
        return redirect('assignment_detail', pk=recipient.assignment_id) if variant.assigned_only else redirect('variant', pk=pk)
    # Unique constraint also prevents two simultaneous active attempts.
    attempt, _ = ExamAttempt.objects.get_or_create(user=request.user, variant=variant, submitted_at=None, defaults={'deadline': deadline})
    return redirect('exam', pk=attempt.pk)


@login_required
@transaction.atomic
def exam(request, pk):
    from .python_answers import ExamAnswerForm
    attempt = get_object_or_404(ExamAttempt.objects.select_for_update().select_related('variant'), pk=pk, user=request.user)
    if attempt.submitted_at:
        return redirect('result', pk=pk)
    if timezone.now() >= attempt.deadline:
        finish_attempt(attempt)
        messages.info(request, 'Время закончилось. Проверены последние сохранённые ответы.')
        return redirect('result', pk=pk)
    items = list(attempt.variant.items.all())
    recipient = None
    if attempt.variant.assigned_only:
        from .models import AssignmentRecipient
        recipient = get_object_or_404(AssignmentRecipient.objects.select_related('assignment'), assignment__variant=attempt.variant, student=request.user)
        if not recipient.assignment.active:
            messages.error(request, 'Назначение закрыто преподавателем.')
            return redirect('assignment_detail', pk=recipient.assignment_id)
    answers = {a.item_id: a for a in attempt.answers.all()}
    valid_code = True
    for item in items:
        saved = answers.get(item.pk)
        item.value = posted_answer(request, item) if request.method == 'POST' else saved.answer if saved else ''
        item.grid = answer_grid(item, item.value)
        # Homework always accepts an optional program; required-code tasks retain their rule.
        if recipient and recipient.assignment.kind == 'homework' and item.code_mode == 'off':
            item.code_mode = 'optional'
        required_code = request.method == 'POST' and request.POST.get('action') == 'submit' and bool(item.value.strip()) and item.code_mode == 'required'
        # Required only on final submission; browser validation must still allow saving drafts.
        item.code_form = ExamAnswerForm(request.POST if request.method == 'POST' else None, mode=item.code_mode, required=required_code, use_required_attribute=False, prefix=f'code_{item.pk}', initial={'python_code': saved.python_code if saved else '', 'comment': saved.comment if saved else ''})
        if request.method == 'POST' and not item.code_form.is_valid():
            valid_code = False
    if request.method == 'POST' and valid_code:
        if not claim_submission(request, f'exam:{pk}'):
            return redirect('exam', pk=pk)
        for item in items:
            ExamAnswer.objects.update_or_create(attempt=attempt, item=item, defaults={'answer': item.value, 'python_code': item.code_form.cleaned_data.get('python_code', ''), 'comment': item.code_form.cleaned_data['comment']})
        if request.POST.get('action') == 'submit':
            finish_attempt(attempt)
            return redirect('result', pk=pk)
        messages.success(request, 'Ответы сохранены. Можно продолжить позже до окончания времени.')
        return redirect('exam', pk=pk)
    if request.method == 'POST' and not valid_code:
        messages.error(request, 'Проверьте код и комментарии в отмеченных заданиях. Ответы пока не сохранены; введённые данные остались в форме.')
    remaining = max(0, math.ceil((attempt.deadline - timezone.now()).total_seconds() / 60))
    template = 'exam_station.html' if attempt.variant.scoring == 'ege' else 'exam.html'
    return render(request, template, {'submit_token': form_token(request.user, f'exam:{pk}'), 'attempt': attempt, 'items': items, 'remaining': remaining, 'work': recipient.assignment if recipient else None, 'attempt_number': ExamAttempt.objects.filter(user=attempt.user, variant=attempt.variant, pk__lte=attempt.pk).count(), 'nav': 'homework' if recipient and recipient.assignment.kind == 'homework' else 'variants'})


@login_required
@require_POST
@transaction.atomic
def exam_save(request, pk):
    """Save one answer of an open attempt (KEGE station «Сохранить ответ»); code and comments are kept."""
    attempt = get_object_or_404(ExamAttempt.objects.select_for_update().select_related('variant'), pk=pk, user=request.user, submitted_at=None)
    if timezone.now() >= attempt.deadline:
        return JsonResponse({'error': 'Время вышло. Завершите экзамен — проверятся сохранённые ответы.'}, status=409)
    if attempt.variant.assigned_only:
        from .models import AssignmentRecipient
        recipient = AssignmentRecipient.objects.select_related('assignment').filter(assignment__variant=attempt.variant, student=request.user).first()
        if recipient is None or not recipient.assignment.active:
            return JsonResponse({'error': 'Назначение закрыто преподавателем.'}, status=409)
    item = get_object_or_404(attempt.variant.items, pk=request.POST.get('item') if str(request.POST.get('item', '')).isdigit() else 0)
    value = posted_answer(request, item)
    ExamAnswer.objects.update_or_create(attempt=attempt, item=item, defaults={'answer': value})
    return JsonResponse({'ok': True, 'value': value, 'answered': attempt.answers.exclude(answer='').count()})


@login_required
def result(request, pk):
    qs = ExamAttempt.objects.select_related('variant', 'user')
    if not request.user.is_staff:
        qs = qs.filter(user=request.user)
    elif not request.user.is_superuser:
        qs = qs.filter(Q(user=request.user) | Q(variant__assigned_only=True, variant__assignment__teacher=request.user) | Q(variant__assigned_only=False, user__assigned_teachers__teacher=request.user)).distinct()
    attempt = get_object_or_404(qs, pk=pk, submitted_at__isnull=False)
    total = attempt.variant.items.count()
    recipient = None
    if attempt.variant.assigned_only:
        from .models import AssignmentRecipient
        recipient = AssignmentRecipient.objects.select_related('assignment').filter(assignment__variant=attempt.variant, student=attempt.user).first()
    from .assignments import assignment_block
    from .services import max_points
    answers = list(attempt.answers.select_related('item').order_by('item__position'))
    for a in answers:
        a.max_points = max_points(a.item.number) if attempt.variant.scoring == 'ege' else 1
    max_primary = sum(a.max_points for a in answers)
    return render(request, 'result.html', {'attempt': attempt, 'answers': answers, 'total': total, 'percent': round(attempt.score / total * 100) if total else 0,
        'ege': attempt.variant.scoring == 'ege', 'max_primary': max_primary, 'full_count': sum(1 for a in answers if a.correct), 'show_answers': not attempt.variant.hide_answers or request.user.is_staff,
        'work': recipient.assignment if recipient else None, 'retry_block': assignment_block(recipient) if recipient else '',
        'attempt_number': ExamAttempt.objects.filter(user=attempt.user, variant=attempt.variant, pk__lte=attempt.pk).count(),
        'start_token': form_token(request.user, f'start-exam:{attempt.variant_id}'), 'nav': 'homework' if recipient and recipient.assignment.kind == 'homework' else 'variants'})


@login_required
def course(request):
    sections = Section.objects.prefetch_related('lessons')
    completed = set(LessonProgress.objects.filter(user=request.user, passed=True).values_list('lesson_id', flat=True))
    count = Lesson.objects.filter(published=True).count()
    return render(request, 'course.html', {'sections': sections, 'completed': completed, 'count': count, 'percent': round(len(completed) / count * 100) if count else 0, 'nav': 'course'})


@login_required
def lesson(request, pk):
    from .course_services import can_edit, available_courses
    from .course_views import player
    lesson_obj = get_object_or_404(Lesson.objects.select_related('section__course'), pk=pk)
    course_obj = lesson_obj.section.course
    if course_obj:
        if not available_courses(request.user).filter(pk=course_obj.pk).exists():
            raise Http404
        first = lesson_obj.steps.first() if can_edit(request.user, course_obj) else lesson_obj.steps.filter(published=True).first()
        if first:
            from .models import Enrollment
            if not can_edit(request.user, course_obj) and not Enrollment.objects.filter(user=request.user, course=course_obj).exists():
                return redirect('course_overview', pk=course_obj.pk)
            return player(request, first.pk)
    lesson = get_object_or_404(Lesson, pk=pk, published=True)
    questions = list(lesson.questions.all())
    feedback = None
    if request.method == 'POST' and questions:
        feedback = []
        for q in questions:
            selected = request.POST.get(f'q_{q.pk}', '')
            correct = selected == str(q.correct_index)
            feedback.append({'question': q, 'correct': correct})
        score = sum(row['correct'] for row in feedback)
        progress, _ = LessonProgress.objects.get_or_create(user=request.user, lesson=lesson)
        progress.score = max(progress.score, score)
        progress.passed = progress.passed or score == len(questions)
        progress.save()
    return render(request, 'lesson.html', {'lesson': lesson, 'questions': questions, 'feedback': feedback, 'passed': LessonProgress.objects.filter(user=request.user, lesson=lesson, passed=True).exists(), 'nav': 'course'})


@login_required
def stats(request):
    expire_attempts(request.user)
    topics, solved = topic_stats(request.user)
    return render(request, 'stats.html', {'topics': topics, 'solved_count': len(solved), 'attempts': ExamAttempt.objects.filter(user=request.user, submitted_at__isnull=False).select_related('variant'), 'nav': 'stats'})


@staff_required
def teacher(request):
    expire_attempts()
    results = ExamAttempt.objects.filter(submitted_at__isnull=False).select_related('user', 'variant')
    results = results.filter(user__assigned_teachers__teacher=request.user).distinct().annotate(item_count=Count('variant__items', distinct=True)).order_by('-submitted_at', '-pk')
    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="results.csv"'
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        writer.writerow(['Ученик', 'Работа', 'Верно', 'Всего', 'Сдано'])
        def safe_cell(value):
            s = str(value)
            return "'" + s if s.startswith(('=', '+', '-', '@', '\t', '\r')) else s
        for a in results:
            writer.writerow([safe_cell(a.user.username), safe_cell(a.variant.title), a.score, a.item_count, timezone.localtime(a.submitted_at).isoformat()])
        return response
    from .workspace import home_context
    context = home_context(request.user)
    context.update({'variants': Variant.objects.filter(creator=request.user, assigned_only=False).annotate(task_count=Count('items'))[:12], 'results': results[:8], 'nav': 'teacher'})
    return render(request, 'teacher.html', context)


@staff_required
def variant_create(request):
    form = VariantForm(request.POST or None, initial={'kind': request.GET.get('kind', 'exam')})
    from .assignments import own_groups
    form.fields['group'].queryset = own_groups(request.user)
    if request.method == 'POST' and form.is_valid():
        try:
            variant = create_variant(creator=request.user, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Работа создана. Её можно открыть или распечатать.')
            return redirect('variant', pk=variant.pk)
    return render(request, 'variant_form.html', {'form': form, 'nav': 'teacher'})


@staff_required
def task_create(request):
    form = TaskForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        task = form.save()
        messages.success(request, f'Задание #{task.pk} добавлено.')
        return redirect('teacher')
    return render(request, 'task_form.html', {'form': form, 'nav': 'teacher'})


@staff_required
@require_POST
def publish_variant(request, pk):
    variant = get_object_or_404(Variant.objects.all() if request.user.is_superuser else Variant.objects.filter(creator=request.user), pk=pk)
    variant.published = not variant.published
    variant.save(update_fields=['published'])
    return redirect('teacher')


@staff_required
def print_variant(request, pk):
    variant = get_object_or_404(Variant.objects.all() if request.user.is_superuser else Variant.objects.filter(creator=request.user), pk=pk)
    return render(request, 'print.html', {'variant': variant, 'answers': request.GET.get('answers') == '1'})


@login_required
def download(request, kind, pk):
    video = False
    inline_image = False
    if kind in ('homework-image', 'homework-file'):
        from .models import HomeworkSubmission
        obj = get_object_or_404(HomeworkSubmission.objects.select_related('recipient__assignment'), pk=pk)
        if request.user.pk not in (obj.recipient.student_id, obj.recipient.assignment.teacher_id) and not request.user.is_superuser:
            raise Http404
        f = obj.image if kind == 'homework-image' else obj.file
        inline_image = kind == 'homework-image'
    elif kind == 'library':
        from .models import MediaAsset
        if not request.user.is_staff:
            raise Http404
        obj = get_object_or_404(MediaAsset.objects.all() if request.user.is_superuser else MediaAsset.objects.filter(owner=request.user), pk=pk)
        f, video, inline_image = obj.file, obj.kind == 'video', obj.kind == 'image'
    elif kind == 'notebook-cell':
        from .models import NotebookCell
        from .notebooks import notebook_access
        obj = get_object_or_404(NotebookCell.objects.select_related('notebook'), pk=pk)
        notebook_access(request.user, obj.notebook, request.GET.get('step'))
        if obj.kind not in ('image', 'video', 'file'):
            raise Http404
        f = getattr(obj, obj.kind)
        video, inline_image = obj.kind == 'video', obj.kind == 'image'
    elif kind == 'course-block':
        from .models import StepContentBlock
        from .course_views import step_access
        obj = get_object_or_404(StepContentBlock.objects.select_related('step__lesson__section__course'), pk=pk)
        step_access(request.user, obj.step)
        if not obj.step.use_blocks or obj.kind not in ('image', 'video', 'file'):
            raise Http404
        f = getattr(obj, obj.kind)
        video, inline_image = obj.kind == 'video', obj.kind == 'image'
    elif kind in ('step-video', 'step-image', 'step-file'):
        from .course_views import step_access
        obj = get_object_or_404(Step.objects.select_related('lesson__section__course'), pk=pk)
        step_access(request.user, obj)
        if obj.use_blocks:
            raise Http404
        f = obj.video if kind == 'step-video' else obj.image if kind == 'step-image' else obj.file
        video = kind == 'step-video'
        inline_image = kind == 'step-image'
    elif kind == 'cover':
        from .course_services import available_courses
        obj = get_object_or_404(available_courses(request.user), pk=pk)
        f = obj.cover
        inline_image = True
    elif kind == 'block':
        qs = TaskBlock.objects.all() if request.user.is_staff else TaskBlock.objects.filter(task__published=True)
        obj = get_object_or_404(qs, pk=pk)
        if obj.purpose == 'solution' and not request.user.is_staff and not Practice.objects.filter(user=request.user, task=obj.task).exists():
            raise Http404
        f = obj.image if obj.kind == 'image' else obj.file
        inline_image = obj.kind == 'image'
    elif kind == 'snapshot':
        obj = get_object_or_404(VariantItem, pk=pk, variant__in=visible_variants(request.user))
        try:
            index = int(request.GET.get('block', '-1'))
            if index < 0:
                raise ValueError
            block = obj.blocks[index]
        except (ValueError, IndexError):
            raise Http404
        if block.get('purpose') == 'solution' and not request.user.is_staff and not ExamAttempt.objects.filter(user=request.user, variant=obj.variant, submitted_at__isnull=False).exists():
            raise Http404
        inline_image = block['kind'] == 'image'
        f = block.get('image') if inline_image else block.get('file')
    elif kind == 'task':
        obj = get_object_or_404(Task, pk=pk, published=True)
        f = obj.attachment
    elif kind == 'lesson':
        obj = get_object_or_404(LessonFile, pk=pk, lesson__published=True)
        f = obj.file
        from .course_services import available_courses
        if obj.lesson.section.course_id and not available_courses(request.user).filter(pk=obj.lesson.section.course_id).exists():
            raise Http404
    elif kind == 'video':
        obj = get_object_or_404(Lesson, pk=pk, published=True)
        f = obj.video
        video = True
        from .course_services import available_courses
        if obj.section.course_id and not available_courses(request.user).filter(pk=obj.section.course_id).exists():
            raise Http404
    elif kind == 'item':
        obj = get_object_or_404(VariantItem, pk=pk, variant__in=visible_variants(request.user))
        f = obj.attachment
    else:
        raise Http404
    if not f:
        raise Http404
    name = str(f)
    root = Path(settings.MEDIA_ROOT).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise Http404
    if inline_image and request.GET.get('preview') == '1':
        from .images import preview_path
        path = preview_path(path, root)
    size = path.stat().st_size
    byte_range = request.headers.get('Range', '') if video else ''
    if byte_range:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', byte_range)
        if not match or not any(match.groups()) or not size:
            return HttpResponse(status=416, headers={'Content-Range': f'bytes */{size}'})
        start_text, end_text = match.groups()
        start = int(start_text) if start_text else max(0, size - int(end_text))
        end = min(int(end_text), size - 1) if start_text and end_text else size - 1
        if start >= size or start > end or (not start_text and int(end_text) == 0):
            return HttpResponse(status=416, headers={'Content-Range': f'bytes */{size}'})
        def chunks():
            with path.open('rb') as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    data = stream.read(min(65536, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data
        response = StreamingHttpResponse(chunks(), status=206)
        response['Content-Range'] = f'bytes {start}-{end}/{size}'
        response['Content-Length'] = end - start + 1
    else:
        response = FileResponse(path.open('rb'), as_attachment=not (video or inline_image), filename=path.name)
    if video:
        response['Accept-Ranges'] = 'bytes'
        response['Content-Type'] = 'video/mp4' if path.suffix.lower() == '.mp4' else 'video/webm'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    return response
