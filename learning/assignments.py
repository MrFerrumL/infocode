"""Assignments use private, immutable variant snapshots and explicit recipients."""
from datetime import timedelta
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import (LearningAssignment, AssignmentRecipient, HomeworkSubmission, Notification,
                     StudyGroup, Variant, VariantItem, Topic, Task, Lesson, ExamAttempt, Enrollment)
from .forms import UploadModelForm
from .views import staff_required
from .security import claim_submission, form_token
from .python_answers import work_code_policy_field


def own_groups(user):
    return StudyGroup.objects.all() if user.is_superuser else StudyGroup.objects.filter(teacher=user)


def recipients_for(user):
    from .studio_views import teacher_students
    return teacher_students(user)


def notify(user, title, text='', url=''):
    Notification.objects.create(user=user, title=title[:200], text=text, url=url)


class WorkForm(forms.ModelForm):
    code_policy = work_code_policy_field(initial='optional')
    students = forms.ModelMultipleChoiceField(label='Ученики', queryset=None, required=False, widget=forms.CheckboxSelectMultiple)
    group = forms.ModelChoiceField(label='Или вся группа', queryset=None, required=False)
    source = forms.ModelChoiceField(label='Готовый вариант (создадим независимую копию)', queryset=None, required=False)
    topics = forms.ModelMultipleChoiceField(label='Или номера для генерации', queryset=Topic.objects.all(), required=False, widget=forms.CheckboxSelectMultiple)
    task_ids = forms.CharField(label='Или ID заданий через запятую', required=False)
    count = forms.IntegerField(label='Заданий каждого номера', min_value=1, max_value=3, initial=1, required=False)
    difficulty = forms.ChoiceField(label='Сложность', required=False, choices=[('', 'Любая')] + Task.LEVELS)
    duration = forms.IntegerField(label='Время КР / варианта в минутах', min_value=1, max_value=600, initial=60, required=False)
    class Meta:
        model = LearningAssignment
        fields = ['title', 'kind', 'target', 'description', 'group', 'lesson', 'starts_at', 'due_at', 'max_attempts', 'pass_percent', 'allow_late']
        widgets = {'starts_at': forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}), 'due_at': forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}), 'description': forms.Textarea(attrs={'rows': 3})}
    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields['students'].queryset = recipients_for(user)
        self.fields['group'].queryset = own_groups(user)
        self.fields['source'].queryset = Variant.objects.filter(creator=user, assigned_only=False)
        from .course_services import editable_courses
        self.fields['lesson'].queryset = Lesson.objects.filter(section__course__in=editable_courses(user), published=True, archived_at=None, section__archived_at=None, section__course__published=True)
        self.fields['lesson'].label = 'Урок курса'
        self.initial.setdefault('starts_at', timezone.localtime().replace(second=0, microsecond=0))
        self.initial.setdefault('due_at', timezone.localtime().replace(second=0, microsecond=0) + timedelta(days=7))
    def clean(self):
        d = super().clean()
        if d.get('due_at') and d.get('starts_at') and d['due_at'] <= d['starts_at']:
            self.add_error('due_at', 'Срок сдачи должен быть позже открытия.')
        selected = set(d.get('students', []).values_list('pk', flat=True)) if d.get('students') is not None else set()
        if d.get('group'):
            selected.update(d['group'].students.values_list('pk', flat=True))
        if not selected or not selected.issubset(set(recipients_for(self.user).values_list('pk', flat=True))):
            raise ValidationError('Выберите закреплённых за вами учеников. Все ученики выбранной группы тоже должны быть закреплены за вами.')
        d['recipient_ids'] = selected
        if d.get('kind') == 'required_test' and d.get('target') != 'variant':
            self.add_error('target', 'Обязательная КР должна состоять из заданий / варианта.')
        if d.get('target') == 'variant' and not (d.get('source') or d.get('topics') or d.get('task_ids')):
            raise ValidationError('Выберите вариант, номера или ID заданий.')
        if d.get('target') == 'lesson':
            if not d.get('lesson'):
                self.add_error('lesson', 'Выберите урок.')
            elif d['lesson'].section.course.group_id and not selected.issubset(set(d['lesson'].section.course.group.students.values_list('pk', flat=True))):
                self.add_error('lesson', 'Не у всех выбранных учеников есть доступ к группе курса.')
        return d


class SubmissionForm(UploadModelForm):
    class Meta:
        model = HomeworkSubmission
        fields = ['text', 'image', 'file']
        widgets = {'text': forms.Textarea(attrs={'rows': 6})}
    def clean(self):
        d = super().clean()
        if not any(d.get(k) for k in ('text', 'image', 'file')):
            raise ValidationError('Добавьте текст, фотографию или файл решения.')
        return d


class ReviewForm(forms.ModelForm):
    status = forms.ChoiceField(label='Решение', choices=[('accepted', 'Зачесть'), ('returned', 'Вернуть на доработку')])
    class Meta:
        model = HomeworkSubmission
        fields = ['status', 'score', 'feedback']
        labels = {'score': 'Результат, %'}
    def clean(self):
        d = super().clean()
        if d.get('status') == 'returned' and not d.get('feedback'):
            self.add_error('feedback', 'Напишите, что нужно доработать.')
        if d.get('status') == 'accepted' and (d.get('score') is None or d['score'] < self.instance.recipient.assignment.pass_percent):
            self.add_error('score', 'Для зачёта укажите результат не ниже проходного процента.')
        return d


def assignment_block(recipient, now=None):
    now = now or timezone.now()
    a = recipient.assignment
    if not a.active:
        return 'Назначение закрыто преподавателем.'
    if now < a.starts_at:
        return 'Работа ещё не открыта.'
    if now >= a.due_at and not a.allow_late:
        return 'Срок сдачи истёк.'
    if recipient.completed_at:
        return 'Работа уже зачтена.'
    used = ExamAttempt.objects.filter(user=recipient.student, variant=a.variant).count() if a.target == 'variant' else recipient.submissions.count() if a.target == 'manual' else 0
    active = a.target == 'variant' and ExamAttempt.objects.filter(user=recipient.student, variant=a.variant, submitted_at=None, deadline__gt=now).exists()
    if used >= a.max_attempts + recipient.extra_attempts and not active:
        return 'Попытки закончились. Преподаватель может выдать дополнительную.'
    if a.target == 'manual' and recipient.submissions.filter(status='pending').exists():
        return 'Решение уже на проверке.'
    return ''


def sync_exam_result(attempt):
    recipient = AssignmentRecipient.objects.select_related('assignment').filter(student=attempt.user, assignment__variant=attempt.variant).first()
    if not recipient:
        return
    total = attempt.variant.items.count()
    if total and attempt.score * 100 >= total * recipient.assignment.pass_percent:
        recipient.completed_at = recipient.completed_at or min(attempt.submitted_at, attempt.deadline)
        recipient.save(update_fields=['completed_at'])
    notify(recipient.assignment.teacher, f'Сдана работа: {recipient.assignment.title}', f'{attempt.user.get_full_name() or attempt.user.username}: {attempt.score}/{total}', reverse('assignment_detail', args=[recipient.assignment_id]))


def recipient_status(r):
    a = r.assignment
    if r.completed_at:
        return 'Зачтено с опозданием' if r.completed_at > a.due_at else 'Зачтено'
    submissions = list(r.submissions.all())
    if submissions:
        latest = submissions[0]
        if latest.status in ('pending', 'returned'):
            return latest.get_status_display()
    if timezone.now() > a.due_at:
        return 'Просрочено'
    if timezone.now() < a.starts_at:
        return 'Ожидает открытия'
    return 'Не завершено'


@staff_required
def create(request):
    homework_mode = request.resolver_match.url_name == 'homework_create'
    initial = {k: request.GET[k] for k in ('kind', 'source', 'task_ids') if k in request.GET}
    if homework_mode:
        initial['kind'] = 'homework'
    if request.GET.get('student', '').isdigit():
        initial['students'] = [request.GET['student']]
    if request.GET.get('topic', '').isdigit():
        initial['topics'] = list(Topic.objects.filter(number=request.GET['topic']).values_list('pk', flat=True))
    data = request.POST.copy() if request.method == 'POST' else None
    if homework_mode and data is not None:
        data['kind'] = 'homework'
    form = WorkForm(data, user=request.user, initial=initial)
    if homework_mode:
        form.fields['kind'].widget = forms.HiddenInput()
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                if not claim_submission(request, 'assignment-new'):
                    return redirect('journal')
                a = form.save(commit=False)
                a.teacher = request.user
                d = form.cleaned_data
                if a.target == 'variant':
                    source = d.get('source')
                    if source:
                        if not source.items.exists():
                            raise ValidationError('В исходном варианте нет заданий.')
                        v = Variant.objects.create(creator=request.user, title=a.title, kind='test' if a.kind == 'required_test' else source.kind, duration=d.get('duration') or source.duration, assigned_only=True, published=True)
                        v.topics.set(source.topics.all())
                        VariantItem.objects.bulk_create([VariantItem(variant=v, **{f: getattr(i, f) for f in ('task_id', 'position', 'number', 'statement', 'answer', 'code_mode', 'solution', 'attachment', 'blocks')}) for i in source.items.all()])
                    else:
                        from .services import create_variant
                        v = create_variant(creator=request.user, title=a.title, topics=d['topics'], task_ids=d['task_ids'], count=d.get('count') or 1, difficulty=d['difficulty'], duration=d.get('duration') or 60, kind='test' if a.kind == 'required_test' else 'exam', published=True)
                        v.assigned_only = True
                        v.save(update_fields=['assigned_only'])
                    if d.get('code_policy'):
                        v.items.update(code_mode=d['code_policy'])
                    a.variant = v
                if a.target != 'lesson':
                    a.lesson = None
                a.save()
                AssignmentRecipient.objects.bulk_create([AssignmentRecipient(assignment=a, student_id=pk) for pk in d['recipient_ids']])
                Notification.objects.bulk_create([Notification(user_id=pk, title=f'{a.get_kind_display()}: {a.title}'[:200], text=f'Сдать до {timezone.localtime(a.due_at):%d.%m.%Y %H:%M}', url=reverse('assignment_detail', args=[a.pk])) for pk in d['recipient_ids']])
            messages.success(request, 'Работа назначена. Состав заданий зафиксирован.')
            return redirect('assignment_detail', pk=a.pk)
        except ValidationError as exc:
            form.add_error(None, exc)
    return render(request, 'platform/work_form.html', {'form': form, 'homework_mode': homework_mode,
        'submit_token': form_token(request.user, 'assignment-new'), 'nav': 'teacher',
        'workspace_section': 'homework' if homework_mode else 'works'})


@login_required
def mine(request):
    from .services import expire_attempts
    expire_attempts(request.user)
    rows = AssignmentRecipient.objects.filter(student=request.user).select_related('assignment').prefetch_related('submissions')
    rows = list(rows.order_by('completed_at', 'assignment__due_at'))
    for r in rows:
        r.state = recipient_status(r)
    return render(request, 'platform/my_work.html', {'rows': rows, 'nav': 'assignments'})


@login_required
@transaction.atomic
def detail(request, pk):
    a = get_object_or_404(LearningAssignment.objects.select_related('teacher', 'variant', 'lesson__section__course'), pk=pk)
    teacher = request.user.is_staff and (a.teacher_id == request.user.pk or request.user.is_superuser)
    r = AssignmentRecipient.objects.select_for_update().filter(assignment=a, student=request.user).first()
    if not teacher and not r:
        raise PermissionDenied
    form = SubmissionForm(request.POST or None, request.FILES or None)
    block = assignment_block(r) if r else ''
    if request.method == 'POST':
        action = request.POST.get('action', 'submit')
        if teacher and action in ('extra', 'close', 'open'):
            if action == 'extra':
                recipient = get_object_or_404(AssignmentRecipient.objects.select_for_update(), pk=request.POST.get('recipient'), assignment=a)
                recipient.extra_attempts += 1
                recipient.save(update_fields=['extra_attempts'])
                notify(recipient.student, 'Выдана дополнительная попытка', a.title, reverse('assignment_detail', args=[pk]))
            else:
                a.active = action == 'open'
                a.save(update_fields=['active'])
            return redirect('assignment_detail', pk=pk)
        if not r or block:
            messages.error(request, block or 'Эта форма доступна получателю задания.')
        elif a.target == 'manual' and form.is_valid() and claim_submission(request, f'homework:{pk}'):
            s = form.save(commit=False)
            s.recipient = r
            s.save()
            notify(a.teacher, 'Решение ожидает проверки', f'{request.user.username}: {a.title}', reverse('review_homework', args=[s.pk]))
            return redirect('assignment_detail', pk=pk)
        elif a.target == 'lesson':
            from .course_services import current_progress
            ids = list(a.lesson.steps.filter(published=True, archived_at=None).values_list('pk', flat=True))
            if ids and current_progress(request.user).filter(step_id__in=ids).count() == len(ids):
                r.completed_at = timezone.now()
                r.save(update_fields=['completed_at'])
                notify(a.teacher, 'Урок пройден', f'{request.user.username}: {a.title}', reverse('assignment_detail', args=[pk]))
                return redirect('assignment_detail', pk=pk)
            messages.info(request, 'Сначала завершите все опубликованные шаги урока.')
    recipients = list(a.recipients.select_related('student').prefetch_related('submissions')) if teacher else []
    attempts_by_student = {}
    if teacher and a.variant_id:
        for attempt in ExamAttempt.objects.filter(variant=a.variant, user_id__in=[r.student_id for r in recipients]).annotate(
                code_count=Count('answers', filter=~Q(answers__python_code=''))):
            attempts_by_student.setdefault(attempt.user_id, []).append(attempt)
    for recipient in recipients:
        recipient.state = recipient_status(recipient)
        recipient.exam_attempts = attempts_by_student.get(recipient.student_id, [])
    return render(request, 'platform/work_detail.html', {'work': a, 'recipient': r, 'teacher': teacher, 'recipients': recipients, 'form': form, 'blocked': block, 'submissions': r.submissions.all() if r else [], 'attempts': ExamAttempt.objects.filter(variant=a.variant, user=request.user) if a.variant_id else [], 'submit_token': form_token(request.user, f'homework:{pk}'), 'nav': 'homework' if a.kind == 'homework' else 'teacher' if teacher else 'assignments', 'workspace_section': 'homework' if a.kind == 'homework' else 'works'})


@staff_required
@transaction.atomic
def review(request, pk):
    s = get_object_or_404(HomeworkSubmission.objects.select_for_update().select_related('recipient__assignment', 'recipient__student'), pk=pk)
    if s.recipient.assignment.teacher_id != request.user.pk and not request.user.is_superuser:
        raise PermissionDenied
    form = ReviewForm(request.POST or None, instance=s)
    if request.method == 'POST' and form.is_valid():
        if s.reviewed_at:
            messages.info(request, 'Это решение уже проверено.')
            return redirect('assignment_detail', pk=s.recipient.assignment_id)
        s = form.save(commit=False)
        s.reviewed_at = timezone.now()
        s.save()
        r = AssignmentRecipient.objects.select_for_update().get(pk=s.recipient_id)
        if s.status == 'accepted':
            r.completed_at = s.created_at
        else:
            r.extra_attempts += 1  # A return always permits one revised submission within the deadline.
        r.save(update_fields=['completed_at', 'extra_attempts'])
        notify(r.student, f'{s.get_status_display()}: {s.recipient.assignment.title}', s.feedback, reverse('assignment_detail', args=[r.assignment_id]))
        return redirect('assignment_detail', pk=r.assignment_id)
    return render(request, 'platform/review.html', {'submission': s, 'form': form, 'nav': 'teacher'})


def safe_cell(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else value


@staff_required
def journal(request):
    from .services import expire_attempts
    expire_attempts()
    students = recipients_for(request.user)
    groups = own_groups(request.user)
    group = request.GET.get('group', '')
    if group.isdigit():
        selected = get_object_or_404(groups, pk=group)
        students = students.filter(studygroup=selected)
    works = list(LearningAssignment.objects.filter(teacher=request.user).order_by('-created_at')[:50])
    recipients = {(r.student_id, r.assignment_id): r for r in AssignmentRecipient.objects.filter(assignment__in=works, student__in=students).select_related('assignment').prefetch_related('submissions')}
    rows = []
    for student in students:
        cells = []
        for a in works:
            r = recipients.get((student.pk, a.pk))
            cells.append({'work': a, 'recipient': r, 'state': recipient_status(r) if r else '—'})
        rows.append({'student': student, 'cells': cells})
    if request.GET.get('export') == 'csv':
        import csv
        response = HttpResponse(content_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="journal.csv"'})
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        writer.writerow(['Ученик'] + [safe_cell(a.title) for a in works])
        for row in rows:
            writer.writerow([safe_cell(row['student'].get_full_name() or row['student'].username)] + [c['state'] for c in row['cells']])
        return response
    return render(request, 'platform/journal.html', {'works': works, 'rows': rows, 'groups': groups, 'group': group, 'nav': 'teacher'})
