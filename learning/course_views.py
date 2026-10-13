from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F, Q, Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from .models import Course, Lesson, Step, StepContentBlock, Enrollment, StepProgress, StepSubmission, StepComment
from .course_services import available_courses, can_edit, published_steps, current_progress, sync_lesson_progress
from .services import check_answer
from .security import form_token, claim_submission
from .python_answers import PythonCodeForm

ASSESSED = ('quiz', 'answer', 'numeric', 'gaps', 'matching')


def evaluate_step(step, data):
    if step.kind == 'quiz':
        try:
            chosen = sorted(set(int(v) for v in data.getlist('choice')))
        except ValueError:
            chosen = []
        correct = bool(chosen) and chosen == sorted(set(step.correct_choices))
        return correct, ', '.join(step.options[n] for n in chosen if 0 <= n < len(step.options)), 100 if correct else 0
    if step.kind in ('gaps', 'matching'):
        answers = [data.get(f'pair_{n}', '')[:250] for n in range(len(step.pairs))]
        score = sum(check_answer(value, pair['answer']) for value, pair in zip(answers, step.pairs))
        percent = score * 100 / len(step.pairs) if step.pairs else 0
        return bool(step.pairs) and percent >= step.pass_percent, ' | '.join(answers), round(percent)
    value = data.get('answer', '')[:250]
    if step.kind == 'numeric':
        from decimal import Decimal, InvalidOperation
        try:
            number = Decimal(value.replace(',', '.'))
            expected = Decimal(step.answer.replace(',', '.'))
            correct = number.is_finite() and expected.is_finite() and abs(number-expected) <= step.tolerance
        except (InvalidOperation, ValueError, OverflowError):
            correct = False
    else:
        correct = check_answer(value, step.answer)
    return correct, value, 100 if correct else 0


def step_access(user, step):
    course = step.lesson.section.course
    if not course or course.archived_at or step.archived_at or step.lesson.archived_at or step.lesson.section.archived_at:
        raise Http404
    if can_edit(user, course):
        return course
    if not available_courses(user).filter(pk=course.pk).exists() or not step.published or not step.lesson.published:
        raise Http404
    if step.use_blocks and step.content_blocks.filter(kind='notebook').filter(
            Q(notebook__isnull=True) | Q(notebook__published=False) | Q(notebook__archived_at__isnull=False)).exists():
        raise Http404
    if not Enrollment.objects.filter(user=user, course=course).exists():
        raise PermissionDenied('Сначала запишитесь на курс.')
    if course.sequential:
        previous_ids = []
        for pk in published_steps(course).values_list('pk', flat=True):
            if pk == step.pk:
                break
            previous_ids.append(pk)
        if current_progress(user).filter(step_id__in=previous_ids).count() != len(previous_ids):
            raise PermissionDenied('Сначала завершите предыдущие шаги курса.')
    return course


def course_data(user, course):
    steps = list(published_steps(course))
    completed = set(current_progress(user).filter(step__in=steps).values_list('step_id', flat=True))
    course.step_total = len(steps)
    course.step_done = len(completed)
    course.percent = round(len(completed)/len(steps)*100) if steps else 0
    course.lesson_total = Lesson.objects.filter(section__course=course, published=True).count()
    course.next_step = next((s for s in steps if s.pk not in completed), steps[0] if steps else None)
    return steps, completed


@login_required
def courses(request):
    courses = list(available_courses(request.user))
    enrolled = set(Enrollment.objects.filter(user=request.user).values_list('course_id', flat=True))
    for course in courses:
        course_data(request.user, course)
        course.enrolled = course.pk in enrolled
    return render(request, 'courses/list.html', {'courses': courses, 'nav': 'course'})


@login_required
def overview(request, pk):
    course = get_object_or_404(available_courses(request.user), pk=pk)
    steps, completed = course_data(request.user, course)
    return render(request, 'courses/overview.html', {'course': course, 'sections': course.sections.prefetch_related('lessons__steps'), 'completed': completed, 'enrolled': Enrollment.objects.filter(user=request.user, course=course).exists(), 'editor': can_edit(request.user, course), 'nav': 'course'})


@login_required
@require_POST
def enroll(request, pk):
    course = get_object_or_404(available_courses(request.user), pk=pk)
    Enrollment.objects.get_or_create(user=request.user, course=course)
    course_data(request.user, course)
    if course.next_step:
        return redirect('learn_step', pk=course.next_step.pk)
    messages.info(request, 'В этом курсе пока нет опубликованных шагов.')
    return redirect('course_overview', pk=course.pk)


@login_required
@transaction.atomic
def player(request, pk):
    if request.method == 'POST':
        from django.contrib.auth import get_user_model
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
    step = get_object_or_404(Step.objects.select_related('lesson__section__course').prefetch_related(
        Prefetch('content_blocks', queryset=StepContentBlock.objects.select_related('notebook').prefetch_related('notebook__cells'))), pk=pk)
    course = step_access(request.user, step)
    editor = can_edit(request.user, course)
    steps = list(step.lesson.steps.all() if editor else step.lesson.steps.filter(published=True))
    all_steps = list(Step.objects.filter(lesson__section__course=course).order_by('lesson__section__order', 'lesson__section_id', 'lesson__order', 'lesson_id', 'position', 'id')) if editor else list(published_steps(course))
    index = next(i for i,s in enumerate(all_steps) if s.pk == step.pk)
    next_step = all_steps[index+1] if index+1 < len(all_steps) else None
    previous = all_steps[index-1] if index else None
    feedback = request.session.pop(f'step_feedback_{step.pk}', None)
    progress = StepProgress.objects.select_for_update().filter(user=request.user, step=step, revision=step.revision).first()
    limited = bool(step.max_attempts and progress and progress.attempts >= step.max_attempts)
    previous_code = StepSubmission.objects.filter(user=request.user, step=step, revision=step.revision).first() if step.code_mode != 'off' else None
    code_form = PythonCodeForm(request.POST if request.method == 'POST' else None,
                               mode=step.code_mode if step.kind in ASSESSED else 'off',
                               initial={'python_code': previous_code.python_code if previous_code else ''})
    code_invalid = (request.method == 'POST' and request.POST.get('action', 'answer') == 'answer'
                    and step.kind in ASSESSED and not code_form.is_valid())
    if request.method == 'POST' and not code_invalid:
        action = request.POST.get('action', 'answer')
        if not claim_submission(request, f'step:{pk}'):
            return redirect('learn_step', pk=pk)
        if action == 'comment':
            text = request.POST.get('comment', '').strip()
            if text and len(text) <= 3000:
                StepComment.objects.create(step=step, user=request.user, text=text)
                messages.success(request, 'Комментарий добавлен.')
            else:
                messages.error(request, 'Комментарий должен содержать от 1 до 3000 символов.')
            return redirect('learn_step', pk=pk)
        if action in ('complete', 'answer'):
            if limited and not editor:
                messages.error(request, 'Лимит попыток исчерпан.')
                return redirect('learn_step', pk=pk)
            correct = False
            submitted = ''
            percent = 0
            if step.kind in ASSESSED and action == 'answer':
                correct, submitted, percent = evaluate_step(step, request.POST)
            elif step.kind not in ASSESSED and action == 'complete':
                correct = True
            else:
                raise PermissionDenied
            feedback = {'correct': correct, 'percent': percent}
            if not editor:
                if not progress:
                    progress, _ = StepProgress.objects.update_or_create(user=request.user, step=step, defaults={'revision': step.revision, 'completed': False, 'attempts': 0, 'points': 0})
                if step.kind in ASSESSED:
                    progress.attempts += 1
                    StepSubmission.objects.create(user=request.user, step=step, revision=step.revision, answer=submitted, python_code=code_form.cleaned_data.get('python_code', ''), correct=correct)
                progress.completed = progress.completed or correct
                progress.points = step.points if progress.completed and step.kind in ASSESSED else 0
                progress.save()
                sync_lesson_progress(request.user, step.lesson)
                limited = bool(step.max_attempts and progress.attempts >= step.max_attempts)
            if action == 'complete':
                return redirect('learn_step', pk=next_step.pk) if next_step else redirect('course_overview', pk=course.pk)
            request.session[f'step_feedback_{step.pk}'] = feedback
            return redirect('learn_step', pk=step.pk)
    if not editor:
        Enrollment.objects.filter(user=request.user, course=course).update(last_step=step)
    completed = set(current_progress(request.user).values_list('step_id', flat=True))
    sections = list(course.sections.prefetch_related('lessons__steps'))
    public_count = published_steps(course).count()
    completed_count = current_progress(request.user).filter(step__in=published_steps(course)).count()
    return render(request, 'courses/player.html', {'code_form': code_form, 'answer_value': request.POST.get('answer', '')[:250] if code_invalid else '', 'selected_choices': [int(v) for v in request.POST.getlist('choice') if v.isdecimal() and len(v) < 4] if code_invalid else [], 'pair_rows': [{'prompt': p['prompt'], 'value': request.POST.get(f'pair_{i}', '')[:250] if code_invalid else ''} for i, p in enumerate(step.pairs)], 'submit_token': form_token(request.user, f'step:{pk}'), 'assessed': step.kind in ASSESSED, 'pair_prompts': [p['prompt'] for p in step.pairs], 'match_options': sorted({p['answer'] for p in step.pairs}, key=lambda s: s.casefold()), 'course': course, 'step': step, 'lesson': step.lesson, 'steps': steps, 'sections': sections, 'editor': editor, 'completed': completed, 'next_step': next_step, 'previous': previous, 'progress': progress, 'feedback': feedback, 'limited': limited, 'multiple': step.quiz_mode == 'multiple' or (step.quiz_mode == 'auto' and len(step.correct_choices)>1), 'reveal': bool(editor or (progress and progress.completed) or limited), 'comments': step.comments.filter(hidden=False).select_related('user'), 'attempts': StepSubmission.objects.filter(user=request.user, step=step, revision=step.revision)[:5], 'percent': round(completed_count/public_count*100) if public_count else 0, 'nav': 'course'})


@login_required
@require_POST
def hide_comment(request, pk):
    comment=get_object_or_404(StepComment.objects.select_related('step__lesson__section__course'),pk=pk)
    if comment.user_id != request.user.id and not can_edit(request.user,comment.step.lesson.section.course):
        raise PermissionDenied
    comment.hidden=True
    comment.save(update_fields=['hidden'])
    return redirect('learn_step',pk=comment.step_id)
