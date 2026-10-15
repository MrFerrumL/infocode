from collections import defaultdict
from datetime import datetime, time, timedelta
from django.contrib import messages
from django.db.models import Count, Min, Max, Q, F, Sum
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from .views import staff_required
from .models import (Practice, Topic, StepSubmission, StepProgress, Enrollment, ExamAttempt,
                     TeacherNote, AssignmentRecipient, HomeworkSubmission, Step)
from .studio_views import teacher_students
from .assignments import own_groups, recipient_status


def period(request):
    today = timezone.localdate()
    try:
        end = datetime.strptime(request.GET.get('to', str(today)), '%Y-%m-%d').date()
        start = datetime.strptime(request.GET.get('from', str(today - timedelta(days=29))), '%Y-%m-%d').date()
        if start > end or (end-start).days > 365:
            raise ValueError
    except ValueError:
        start, end = today-timedelta(days=29), today
        messages.info(request, 'Период сброшен на 30 дней. Можно выбрать до 366 дней.')
    tz = timezone.get_current_timezone()
    return start, end, timezone.make_aware(datetime.combine(start, time.min), tz), timezone.make_aware(datetime.combine(end+timedelta(days=1), time.min), tz)


def aggregate_students(students, start=None, end=None):
    """Batch queries, independent of the number of students; no per-cell database work."""
    students = list(students)
    ids = [s.pk for s in students]
    qs = Practice.objects.filter(user_id__in=ids)
    if start:
        qs = qs.filter(created_at__gte=start, created_at__lt=end)
    first_ids = Practice.objects.filter(user_id__in=ids).order_by().values('user_id', 'task_id').annotate(first=Min('id')).values('first')
    counts = {r['user_id']: r for r in qs.order_by().values('user_id').annotate(total=Count('id'), hits=Count('id', filter=Q(correct=True)), solved=Count('task_id', distinct=True, filter=Q(correct=True)), first_total=Count('id', filter=Q(pk__in=first_ids)), first_correct=Count('id', filter=Q(pk__in=first_ids, correct=True)))}
    matrix = {(r['user_id'], r['task__topic__number']): r for r in qs.order_by().values('user_id', 'task__topic__number').annotate(total=Count('id'), correct=Count('id', filter=Q(correct=True)))}
    progress = {r['user_id']: r for r in StepProgress.objects.filter(user_id__in=ids, completed=True, revision=F('step__revision'), step__published=True, step__archived_at=None, step__lesson__published=True, step__lesson__archived_at=None, step__lesson__section__archived_at=None, step__lesson__section__course__published=True, step__lesson__section__course__archived_at=None).values('user_id').annotate(steps=Count('id'), points=Sum('points'))}
    activity = defaultdict(list)
    for model, field in ((Practice, 'created_at'), (StepProgress, 'updated_at'), (ExamAttempt, 'started_at')):
        for row in model.objects.filter(user_id__in=ids).order_by().values('user_id').annotate(last=Max(field)):
            activity[row['user_id']].append(row['last'])
    for row in HomeworkSubmission.objects.filter(recipient__student_id__in=ids).order_by().values('recipient__student_id').annotate(last=Max('created_at')):
        activity[row['recipient__student_id']].append(row['last'])
    workcounts = dict(ExamAttempt.objects.filter(user_id__in=ids, submitted_at__isnull=False).order_by().values('user_id').annotate(n=Count('id')).values_list('user_id', 'n'))
    rows = []
    for s in students:
        c = counts.get(s.pk, {})
        s.total = c.get('total', 0)
        s.correct_count = c.get('hits', 0)
        s.solved_count = c.get('solved', 0)
        s.first_total = c.get('first_total', 0)
        s.first_accuracy = round(c.get('first_correct', 0)*100/s.first_total) if s.first_total else None
        s.repeats = s.total-s.first_total
        s.accuracy = round(s.correct_count*100/s.total) if s.total else None
        s.step_count = progress.get(s.pk, {}).get('steps', 0)
        s.course_points = progress.get(s.pk, {}).get('points', 0)
        s.work_count = workcounts.get(s.pk, 0)
        s.activity = max(activity[s.pk], default=None)
        s.inactive = not s.activity or s.activity < timezone.now()-timedelta(days=7)
        s.cells = []
        for number in range(1, 28):
            cell = matrix.get((s.pk, number), {'total': 0, 'correct': 0}).copy()
            cell['number'] = number
            cell['percent'] = round(cell['correct']*100/cell['total']) if cell['total'] else None
            cell['state'] = 'empty' if not cell['total'] else 'good' if cell['percent'] >= 80 else 'medium' if cell['percent'] >= 50 else 'weak'
            s.cells.append(cell)
        s.weak = [c for c in s.cells if c['total'] >= 3 and c['percent'] < 50]
        rows.append(s)
    return rows, qs


@staff_required
def dashboard(request):
    students = teacher_students(request.user).order_by('first_name', 'username')
    groups = own_groups(request.user)
    group = request.GET.get('group', '')
    if group.isdigit():
        students = students.filter(studygroup=get_object_or_404(groups, pk=group))
    start, end, lower, upper = period(request)
    rows, practices = aggregate_students(students, lower, upper)
    from django.db.models.functions import TruncDate
    daily = {r['day']: r for r in practices.order_by().annotate(day=TruncDate('created_at')).values('day').annotate(total=Count('id'), correct=Count('id', filter=Q(correct=True)))}
    peak = max([r['total'] for r in daily.values()] or [1])
    days = []
    for n in range((end-start).days+1):
        day = start+timedelta(days=n)
        r = daily.get(day, {'total': 0, 'correct': 0})
        days.append({'date': day, **r, 'height': round(r['total']/peak*100), 'accuracy': round(r['correct']/r['total']*100) if r['total'] else None})
    total = sum(s.total for s in rows)
    correct = sum(s.correct_count for s in rows)
    alerts = [{'student': s, 'text': 'Нет учебной активности 7 дней или больше.'} for s in rows if s.inactive]
    alerts += [{'student': s, 'text': 'Нужна практика: номера ' + ', '.join(str(c['number']) for c in s.weak) + '. Менее 50% верных ответов при 3+ попытках.'} for s in rows if s.weak]
    course_totals = dict(Step.objects.filter(published=True, archived_at=None, lesson__published=True, lesson__archived_at=None, lesson__section__archived_at=None, lesson__section__course__published=True, lesson__section__course__archived_at=None).values('lesson__section__course_id').annotate(n=Count('id')).values_list('lesson__section__course_id', 'n'))
    course_progress = {(r['user_id'], r['step__lesson__section__course_id']): r for r in StepProgress.objects.filter(user__in=students, revision=F('step__revision'), step__published=True, step__archived_at=None, step__lesson__published=True, step__lesson__archived_at=None, step__lesson__section__archived_at=None).values('user_id', 'step__lesson__section__course_id').annotate(done=Count('id', filter=Q(completed=True)), last=Max('updated_at'))}
    for e in Enrollment.objects.filter(user__in=students, course__published=True, course__archived_at=None).select_related('user', 'course'):
        p = course_progress.get((e.user_id, e.course_id), {})
        if p.get('done', 0) < course_totals.get(e.course_id, 0) and (p.get('last') or e.joined_at) < timezone.now()-timedelta(days=7):
            alerts.append({'student': e.user, 'text': f'Курс «{e.course.title}»: нет продвижения 7 дней.'})
    return render(request, 'platform/analytics.html', {'rows': rows, 'groups': groups, 'group': group, 'start': start, 'end': end, 'days': days, 'numbers': range(1,28), 'total': total, 'accuracy': round(correct*100/total) if total else None, 'first': sum(s.first_total for s in rows), 'repeats': sum(s.repeats for s in rows), 'alerts': alerts[:100], 'nav': 'teacher'})


@staff_required
def student(request, pk):
    s = get_object_or_404(teacher_students(request.user), pk=pk)
    if request.method == 'POST':
        note = request.POST.get('note', '').strip()
        if 0 < len(note) <= 5000:
            TeacherNote.objects.create(teacher=request.user, student=s, text=note)
            messages.success(request, 'Личная заметка сохранена. Её видите только вы.')
            return redirect('teacher_student', pk=pk)
        messages.error(request, 'Заметка должна содержать от 1 до 5000 символов.')
    start, end, lower, upper = period(request)
    s = aggregate_students([s], lower, upper)[0][0]
    timeline = []
    for p in Practice.objects.filter(user=s, created_at__gte=lower, created_at__lt=upper).select_related('task__topic')[:100]:
        timeline.append({'time': p.created_at, 'title': f'№{p.task.topic.number} · {p.task.title}', 'text': f'Ответ: {p.answer}', 'python_code': p.python_code, 'result': 'Верно' if p.correct else 'Ошибка', 'url': f'/tasks/{p.task_id}/'})
    for p in StepSubmission.objects.filter(user=s, created_at__gte=lower, created_at__lt=upper).select_related('step__lesson')[:100]:
        timeline.append({'time': p.created_at, 'title': f'{p.step.lesson.title} · {p.step.title}', 'text': f'Ответ: {p.answer}', 'python_code': p.python_code, 'result': 'Пройдено' if p.correct else 'Ошибка', 'url': ''})
    for p in ExamAttempt.objects.filter(user=s, submitted_at__gte=lower, submitted_at__lt=upper).select_related('variant').annotate(total=Count('variant__items'))[:100]:
        timeline.append({'time': p.submitted_at, 'title': p.variant.title, 'text': 'Вариант / контрольная', 'result': f'{p.score}/{p.total}', 'url': f'/results/{p.pk}/'})
    for p in HomeworkSubmission.objects.filter(recipient__student=s, recipient__assignment__teacher=request.user, created_at__gte=lower, created_at__lt=upper).select_related('recipient__assignment')[:100]:
        timeline.append({'time': p.created_at, 'title': p.recipient.assignment.title, 'text': p.text[:300], 'python_code': p.python_code, 'result': p.get_status_display(), 'url': f'/teacher/review/{p.pk}/'})
    timeline = sorted(timeline, key=lambda x: x['time'], reverse=True)[:100]
    assignments = list(AssignmentRecipient.objects.filter(student=s, assignment__teacher=request.user).select_related('assignment').prefetch_related('submissions'))
    for r in assignments:
        r.state = recipient_status(r)
    # Compare first and second halves using the same window and explicit sample sizes.
    middle = lower + (upper-lower)/2
    trends = []
    for label, a, b in [('Первая половина периода', lower, middle), ('Вторая половина периода', middle, upper)]:
        c = Practice.objects.filter(user=s, created_at__gte=a, created_at__lt=b).aggregate(total=Count('id'), correct=Count('id', filter=Q(correct=True)))
        trends.append({'label': label, **c, 'percent': round(c['correct']*100/c['total']) if c['total'] else None})
    return render(request, 'platform/student.html', {'student': s, 'start': start, 'end': end, 'timeline': timeline, 'assignments': assignments, 'notes': TeacherNote.objects.filter(teacher=request.user, student=s), 'trends': trends, 'nav': 'teacher'})
