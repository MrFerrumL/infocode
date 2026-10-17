from django.contrib import messages
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, F, Sum, Max
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from .views import staff_required, topic_stats
from .forms import TaskForm, TaskBlockForm, AssignmentForm, CourseForm, SectionForm, LessonForm, StepForm, QuizOptionFormSet
from .models import Task, TaskBlock, Topic, StudentAssignment, Practice, ExamAttempt, StepProgress, StepSubmission, Course, Section, Lesson, Step, Enrollment, StepComment
from .course_services import editable_courses, can_edit, current_progress


def teacher_students(user):
    return User.objects.filter(assigned_teachers__teacher=user, is_staff=False).distinct()


def student_summary(student):
    practices = Practice.objects.filter(user=student)
    total = practices.count()
    progress = current_progress(student).filter(step__published=True, step__lesson__published=True, step__lesson__section__course__published=True)
    student.solved_count = practices.filter(correct=True).values('task').distinct().count()
    student.accuracy = round(practices.filter(correct=True).count()/total*100) if total else 0
    student.step_count = progress.count()
    student.course_points = progress.aggregate(total=Sum('points'))['total'] or 0
    student.work_count = ExamAttempt.objects.filter(user=student, submitted_at__isnull=False).count()
    student.activity = max(filter(None, [student.last_login, practices.aggregate(last=Max('created_at'))['last'], StepProgress.objects.filter(user=student).aggregate(last=Max('updated_at'))['last']]), default=None)
    return student


@staff_required
def students(request):
    qs = teacher_students(request.user)
    query = request.GET.get('q', '').strip()
    if query:
        qs = qs.filter(Q(username__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query))
    page = Paginator(qs.order_by('first_name', 'username'), 20).get_page(request.GET.get('page'))
    from .analytics import aggregate_students
    page.object_list = aggregate_students(page.object_list)[0]
    form = AssignmentForm(request.POST or None)
    if request.method == 'POST':
        if not request.user.is_superuser:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        if form.is_valid():
            StudentAssignment.objects.get_or_create(teacher=request.user, student=form.cleaned_data['student'])
            messages.success(request, 'Ученик закреплён за вами.')
            return redirect('teacher_students')
    return render(request, 'studio/students.html', {'page': page, 'form': form, 'query': query, 'nav': 'teacher'})


@staff_required
def student_detail(request, pk):
    student = get_object_or_404(teacher_students(request.user), pk=pk)
    student_summary(student)
    topics, _ = topic_stats(student)
    return render(request, 'studio/student.html', {'student': student, 'topics': topics, 'works': ExamAttempt.objects.filter(user=student, submitted_at__isnull=False).select_related('variant'), 'progress': current_progress(student).select_related('step__lesson__section__course'), 'submissions': StepSubmission.objects.filter(user=student).select_related('step')[:30], 'nav': 'teacher'})


@staff_required
def task_bank(request):
    from urllib.parse import urlencode
    query = request.GET.get('q', '').strip()[:200]
    tasks = Task.objects.select_related('topic')
    if query:
        tasks = tasks.filter(Q(title__icontains=query) | Q(tags__icontains=query) | Q(subtopic__icontains=query) | ((Q(pk=int(query)) | Q(external_id=int(query))) if query.isdigit() else Q(pk__isnull=True)))
    level = request.GET.get('difficulty', '')
    status = request.GET.get('status', '')
    number = request.GET.get('topic', '')
    if number.isdigit() and 1 <= int(number) <= 27:
        tasks = tasks.filter(topic__number=int(number))
    else:
        number = ''
    if level in dict(Task.LEVELS):
        tasks = tasks.filter(difficulty=level)
    if status in ('draft', 'published'):
        tasks = tasks.filter(published=status == 'published')
    page = Paginator(tasks, 20).get_page(request.GET.get('page'))
    params = {key: value for key, value in {'q': query, 'topic': number, 'difficulty': level, 'status': status}.items() if value}
    return render(request, 'studio/tasks.html', {
        'page': page, 'query': query, 'levels': Task.LEVELS, 'difficulty': level,
        'status': status, 'topic_number': number, 'topics': Topic.objects.all(),
        'pagination_query': urlencode(params), 'return_to': request.get_full_path(), 'nav': 'teacher',
    })


def clone_row(obj, **overrides):
    data = {field.attname: getattr(obj, field.attname) for field in obj._meta.concrete_fields if not field.primary_key and not getattr(field, 'auto_now_add', False)}
    data.update(overrides)
    return type(obj).objects.create(**data)


def clone_step(step, **overrides):
    copied = clone_row(step, revision=1, edit_version=1, published=False, **overrides)
    for block in step.content_blocks.all():
        clone_row(block, step_id=copied.pk)
    return copied


def clone_lesson(lesson, section=None):
    copied = clone_row(lesson, section_id=(section or lesson.section).pk, title=(lesson.title[:165] + ' · копия'), published=False)
    for step in lesson.steps.all():
        clone_step(step, lesson_id=copied.pk)
    return copied


@staff_required
@require_POST
@transaction.atomic
def duplicate_task(request, pk):
    task = get_object_or_404(Task, pk=pk)
    copied = clone_row(task, title=task.title[:185] + ' · копия', published=False, source_system='', external_id=None)
    for block in task.content_blocks.all():
        clone_row(block, task_id=copied.pk)
    messages.success(request, 'Создана копия задания в черновиках.')
    return redirect('task_editor', pk=copied.pk)


@staff_required
@require_POST
@transaction.atomic
def duplicate_course(request, pk):
    course = get_object_or_404(editable_courses(request.user), pk=pk)
    copied = clone_row(course, title=course.title[:165] + ' · копия', owner_id=request.user.pk, published=False)
    for section in course.sections.all():
        section_copy = clone_row(section, course_id=copied.pk)
        for lesson in section.lessons.all():
            clone_lesson(lesson, section_copy)
    messages.success(request, 'Курс скопирован в черновики. Ученики, ответы и прогресс не копируются.')
    return redirect('course_editor', pk=copied.pk)


@staff_required
@transaction.atomic
def task_editor(request, pk=None):
    task = get_object_or_404(Task, pk=pk) if pk else Task(published=False)
    form = TaskForm(request.POST or None, request.FILES or None, instance=task)
    block_form = TaskBlockForm()
    if request.method == 'POST' and form.is_valid():
        task = form.save()
        # Convert optional legacy fields into ordinary reorderable blocks.
        if task.statement:
            TaskBlock.objects.create(task=task, kind='text', text=task.statement, position=0)
            task.statement = ''
        if task.attachment:
            TaskBlock.objects.create(task=task, kind='file', file=task.attachment.name, text='Данные к заданию', position=(task.content_blocks.aggregate(n=Max('position'))['n'] or 0)+1)
            task.attachment = ''
        if task.solution:
            TaskBlock.objects.create(task=task, purpose='solution', kind='text', text=task.solution, position=0)
            task.solution = ''
        for field, purpose, kind in [('statement_image', 'statement', 'image'), ('solution_image', 'solution', 'image'), ('solution_file', 'solution', 'file'), ('solution_code', 'solution', 'code')]:
            value = form.cleaned_data.get(field)
            if value:
                position = (task.content_blocks.filter(purpose=purpose).aggregate(n=Max('position'))['n'] or 0) + 1
                TaskBlock.objects.create(task=task, purpose=purpose, kind=kind, position=position, **{('text' if kind == 'code' else kind): value})
        task.save(update_fields=['statement', 'attachment', 'solution'])
        messages.success(request, 'Задание сохранено. Добавляйте и переставляйте блоки ниже.')
        return redirect('task_editor', pk=task.pk)
    groups = [{'purpose': purpose, 'title': title, 'blocks': task.content_blocks.filter(purpose=purpose) if task.pk else []} for purpose, title in TaskBlock._meta.get_field('purpose').choices]
    return render(request, 'studio/task_editor.html', {'task': task, 'form': form, 'groups': groups, 'kinds': TaskBlock.KINDS, 'nav': 'teacher'})


@staff_required
@transaction.atomic
def block_editor(request, task_pk, pk=None):
    task = get_object_or_404(Task, pk=task_pk)
    purpose = request.GET.get('purpose', 'statement')
    if purpose not in ('statement', 'solution'):
        from django.http import Http404
        raise Http404
    block = get_object_or_404(TaskBlock, task=task, pk=pk) if pk else TaskBlock(task=task, purpose=purpose, position=(task.content_blocks.filter(purpose=purpose).aggregate(n=Max('position'))['n'] or 0)+1)
    form = TaskBlockForm(request.POST or None, request.FILES or None, instance=block, user=request.user, initial={'kind': request.GET.get('kind', block.kind if pk else 'text')})
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Блок сохранён.')
        return redirect('task_editor', pk=task.pk)
    return render(request, 'studio/block_editor.html', {'form': form, 'task': task, 'block': block, 'kinds': TaskBlock.KINDS, 'block_kind': form['kind'].value(), 'nav': 'teacher'})


def move_ordered(obj, siblings, direction, field):
    rows = list(siblings.order_by(field, 'id'))
    index = next(i for i, row in enumerate(rows) if row.pk == obj.pk)
    target = index + (-1 if direction == 'up' else 1)
    if 0 <= target < len(rows):
        rows[index], rows[target] = rows[target], rows[index]
    for position, row in enumerate(rows, 1):
        setattr(row, field, position)
        row.save(update_fields=[field])


@staff_required
@require_POST
@transaction.atomic
def block_action(request, pk):
    block = get_object_or_404(TaskBlock, pk=pk)
    task_id = block.task_id
    action = request.POST.get('action')
    if action == 'delete':
        task = block.task
        from .platform_views import archive
        archive(request.user, block)
        if not task.content_blocks.filter(purpose='statement').exists() and not task.statement and not task.attachment:
            task.published = False
            task.save(update_fields=['published'])
            messages.info(request, 'Последний блок удалён. Задание переведено в черновик.')
    elif action in ('up', 'down'):
        move_ordered(block, block.task.content_blocks.filter(purpose=block.purpose), action, 'position')
    elif action == 'duplicate':
        copy = clone_row(block, position=block.position+1)
        move_ordered(copy, block.task.content_blocks.filter(purpose=block.purpose), 'stay', 'position')
    return redirect('task_editor', pk=task_id)


@staff_required
def courses(request):
    return render(request, 'studio/courses.html', {'courses': editable_courses(request.user).prefetch_related('sections__lessons__steps'), 'nav': 'teacher'})


@staff_required
def course_editor(request, pk=None):
    course = get_object_or_404(editable_courses(request.user), pk=pk) if pk else Course(owner=request.user)
    form = CourseForm(request.POST or None, request.FILES or None, instance=course)
    from .assignments import own_groups
    form.fields['group'].queryset = own_groups(request.user)
    if request.method == 'POST' and form.is_valid():
        course = form.save()
        messages.success(request, 'Настройки курса сохранены.')
        return redirect('course_editor', pk=course.pk)
    sections = list(course.sections.prefetch_related('lessons__steps')) if course.pk else []
    lessons = [lesson for section in sections for lesson in section.lessons.all()]
    steps = [step for lesson in lessons for step in lesson.steps.all()]
    checklist = {'sections': len(sections), 'lessons': len(lessons), 'steps': len(steps), 'live': sum(s.published and s.lesson.published for s in steps), 'drafts': sum(not s.published for s in steps), 'empty_lessons': sum(not lesson.steps.all() for lesson in lessons)}
    return render(request, 'studio/course_editor.html', {'course': course, 'form': form, 'sections': sections, 'checklist': checklist, 'nav': 'teacher'})


@staff_required
def section_editor(request, course_pk, pk=None):
    course = get_object_or_404(editable_courses(request.user), pk=course_pk)
    section = get_object_or_404(Section, pk=pk, course=course) if pk else Section(course=course, order=(course.sections.aggregate(n=Max('order'))['n'] or 0)+1)
    form = SectionForm(request.POST or None, instance=section)
    if request.method == 'POST' and form.is_valid():
        form.save()
        return redirect('course_editor', pk=course.pk)
    return render(request, 'studio/form.html', {'form': form, 'title': 'Настройки модуля', 'back': f'/teacher/courses/{course.pk}/', 'nav': 'teacher'})


@staff_required
def lesson_editor(request, section_pk, pk=None):
    from .lesson_composer import SLIDE_TYPES, SLIDE_TYPES_BY_KEY
    section = get_object_or_404(Section, pk=section_pk, course__in=editable_courses(request.user))
    lesson = get_object_or_404(Lesson, pk=pk, section=section) if pk else Lesson(section=section, order=(section.lessons.aggregate(n=Max('order'))['n'] or 0)+1, theory='')
    form = LessonForm(request.POST or None, instance=lesson)
    if pk:
        form.fields.pop('template')
        form.fields.pop('slide_plan')
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            lesson = form.save()
            if not pk:
                plan = form.cleaned_data.get('slide_plan', [])
                if 'slide_plan' not in request.POST and form.cleaned_data.get('template'):
                    kinds = ['video', 'quiz', 'text', 'file'] if form.cleaned_data['template'] == 'standard' else ['text', 'quiz', 'answer']
                    plan = [{'type': kind, 'title': ''} for kind in kinds]
                for position, row in enumerate(plan, 1):
                    spec = SLIDE_TYPES_BY_KEY[row['type']]
                    kind = spec['kind']
                    Step.objects.create(lesson=lesson, position=position, kind=kind,
                        title=row['title'] or spec['label'], published=False,
                        quiz_mode=spec.get('quiz_mode', 'auto'),
                        options=['Верно', 'Неверно'] if row['type'] == 'boolean' else [],
                        points=1 if kind in ('quiz', 'answer', 'numeric', 'gaps', 'matching') else 0)
                if plan:
                    messages.info(request, 'Слайды созданы в выбранном порядке. Откройте каждый, добавьте содержимое и опубликуйте.')
        return redirect('lesson_editor', section_pk=section.pk, pk=lesson.pk)
    # An unsaved lesson has no reverse relations, including on an invalid POST.
    steps = list(lesson.steps.all()) if lesson.pk else []
    return render(request, 'studio/lesson_editor.html', {
        'form': form, 'lesson': lesson, 'section': section, 'steps': steps,
        'first_step': steps[0] if steps else None, 'slide_types': SLIDE_TYPES, 'nav': 'teacher',
    })


@staff_required
@transaction.atomic
def step_editor(request, lesson_pk, pk=None):
    from .content_editor import block_formset, content_signature, legacy_content
    lesson = get_object_or_404(Lesson, pk=lesson_pk, section__course__in=editable_courses(request.user))
    step = get_object_or_404(Step.objects.select_for_update(), pk=pk, lesson=lesson) if pk else Step(lesson=lesson, position=(lesson.steps.aggregate(n=Max('position'))['n'] or 0)+1)
    editor_version = step.edit_version
    blocks_mode = request.method != 'POST' or 'blocks-TOTAL_FORMS' in request.POST
    old_content = content_signature(list(step.content_blocks.values())) if pk and step.use_blocks else content_signature(legacy_content(step))
    blocks = block_formset(request, step) if blocks_mode else None
    blocks_valid = blocks.is_valid() if blocks is not None and request.method == 'POST' else True
    if blocks_mode:
        step.use_blocks = True
        if request.method == 'POST':
            step._content_kinds = [f.cleaned_data.get('kind') for f in blocks.active_forms()] if blocks_valid else ['text', 'video', 'file']
            step._content_notebooks = [f.cleaned_data.get('notebook') for f in blocks.active_forms() if f.cleaned_data.get('kind') == 'notebook'] if blocks_valid else []
    kind = request.POST.get('kind') if request.method == 'POST' else request.GET.get('kind', step.kind)
    if kind not in dict(Step.KINDS):
        from django.http import Http404
        raise Http404
    preset = request.GET.get('preset', '')
    initial = {'kind': kind}
    if not pk:
        initial.update(points=1 if kind in ('quiz', 'answer', 'numeric', 'gaps', 'matching') else 0, quiz_mode='multiple' if preset == 'multiple' else 'single')
    options = [{'text': text, 'correct': i in step.correct_choices} for i, text in enumerate(step.options)]
    if not pk and preset == 'boolean':
        options = [{'text': 'Верно'}, {'text': 'Неверно'}]
    row_mode = request.method != 'POST' or 'options-TOTAL_FORMS' in request.POST
    post = request.POST.copy() if request.method == 'POST' else None
    adding = request.method == 'POST' and request.POST.get('action') == 'add_option' and kind == 'quiz'
    if adding:
        try:
            post['options-TOTAL_FORMS'] = str(min(50, int(post.get('options-TOTAL_FORMS', 0)) + 1))
        except ValueError:
            pass
    option_formset = QuizOptionFormSet(post, prefix='options', initial=options) if row_mode else None
    form = StepForm(post, request.FILES or None, instance=step, user=request.user, initial=initial, option_formset=option_formset)
    if blocks_mode:
        for field in ('body', 'code', 'image', 'video', 'file', 'asset'):
            form.fields.pop(field, None)
    stale = bool(pk and request.method == 'POST' and blocks_mode and request.POST.get('edit_version') != str(editor_version))
    if stale:
        form.add_error(None, 'Шаг уже изменён в другой вкладке. Скопируйте свой текст и обновите страницу, чтобы не затереть новые изменения.')
    if adding:
        # Adding a row redisplays posted text without saving or validating unfinished content.
        from django.forms.utils import ErrorDict
        form._errors = ErrorDict()
        if request.FILES:
            messages.info(request, 'Строка добавлена. Выберите прикреплённые файлы повторно перед сохранением.')
    if request.method == 'POST' and not adding and not stale and form.is_valid() and blocks_valid and (kind != 'quiz' or option_formset is None or option_formset.is_valid()):
        step = form.save(commit=False)
        content_fields = {'kind', 'body', 'code', 'image', 'video', 'file', 'answer', 'code_mode', 'points', 'max_attempts', 'pairs', 'tolerance', 'pass_percent'}
        original = Step.objects.get(pk=pk) if pk else None
        if step.kind == 'quiz' or (original and original.kind == 'quiz'):
            content_fields.add('quiz_mode')
        options_changed = original and (original.options != step.options or original.correct_choices != step.correct_choices)
        new_content = content_signature([f.cleaned_data for f in blocks.active_forms()]) if blocks_mode else old_content
        uploaded = blocks_mode and any(any(name.startswith(f.prefix + '-') for name in request.FILES) for f in blocks.active_forms())
        if pk and (any(getattr(original, f) != getattr(step, f) for f in content_fields) or options_changed or new_content != old_content or uploaded):
            step.revision += 1
            # Old answers stay in history, but completion applies to the revised step only.
            from .models import LessonProgress
            LessonProgress.objects.filter(lesson=lesson).update(passed=False)
        step.edit_version += 1
        step.save()
        if blocks_mode:
            blocks.persist(step)
        messages.success(request, 'Слайд сохранён.')
        if request.POST.get('action') == 'preview':
            return redirect('learn_step', pk=step.pk)
        return redirect('lesson_editor', section_pk=lesson.section_id, pk=lesson.pk)
    if request.method == 'POST' and request.FILES:
        messages.warning(request, 'Шаг ещё не сохранён. Исправьте отмеченные поля и выберите новые файлы повторно.')
    return render(request, 'studio/step_editor.html', {'form': form, 'options': option_formset if option_formset is not None else QuizOptionFormSet(prefix='options', initial=options), 'blocks': blocks, 'editor_version': request.POST.get('edit_version', editor_version) if stale else editor_version, 'step': step, 'lesson_steps': lesson.steps.only('id', 'title', 'position', 'kind', 'published'), 'step_kind': kind, 'kinds': Step.KINDS, 'lesson': lesson, 'nav': 'teacher'})


@staff_required
@require_POST
@transaction.atomic
def structure_action(request, kind, pk):
    model, field = {'section': (Section, 'order'), 'lesson': (Lesson, 'order'), 'step': (Step, 'position')}.get(kind, (None, None))
    if not model:
        from django.http import Http404
        raise Http404
    obj = get_object_or_404(model, pk=pk)
    course = obj.course if kind == 'section' else obj.section.course if kind == 'lesson' else obj.lesson.section.course
    if not can_edit(request.user, course):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    action = request.POST.get('action')
    if action == 'archive':
        from .platform_views import archive
        archive(request.user, obj)
    elif action in ('up', 'down'):
        siblings = course.sections.all() if kind == 'section' else obj.section.lessons.all() if kind == 'lesson' else obj.lesson.steps.all()
        move_ordered(obj, siblings, action, field)
    elif action == 'duplicate':
        if kind == 'step':
            clone_step(obj, title=obj.title[:165]+' · копия', position=(obj.lesson.steps.aggregate(n=Max('position'))['n'] or 0)+1)
        elif kind == 'lesson':
            copy = clone_lesson(obj)
            copy.order = (obj.section.lessons.aggregate(n=Max('order'))['n'] or 0)+1
            copy.save(update_fields=['order'])
        else:
            copy = clone_row(obj, title=obj.title[:165]+' · копия', order=(course.sections.aggregate(n=Max('order'))['n'] or 0)+1)
            for lesson in obj.lessons.all():
                clone_lesson(lesson, copy)
        messages.success(request, 'Копия создана. Новые уроки и шаги сохранены как черновики.')
    elif action == 'toggle' and kind in ('lesson', 'step'):
        obj.published = not obj.published
        try:
            obj.full_clean()
            fields = ['published']
            if kind == 'step':
                obj.edit_version += 1
                fields.append('edit_version')
            obj.save(update_fields=fields)
        except Exception as exc:
            from django.core.exceptions import ValidationError
            if not isinstance(exc, ValidationError):
                raise
            messages.error(request, 'Не удалось опубликовать: ' + '; '.join(exc.messages))
    if kind == 'step':
        return redirect('lesson_editor', section_pk=obj.lesson.section_id, pk=obj.lesson_id)
    return redirect('course_editor', pk=course.pk)


@staff_required
def course_report(request, pk):
    course = get_object_or_404(editable_courses(request.user), pk=pk)
    rows=[]
    steps=Step.objects.filter(lesson__section__course=course, lesson__published=True, published=True)
    for enrolled in Enrollment.objects.filter(course=course).select_related('user'):
        progress=current_progress(enrolled.user).filter(step__in=steps)
        rows.append({'student':enrolled.user,'done':progress.count(),'total':steps.count(),'points':sum(progress.values_list('points',flat=True))})
    if request.GET.get('export') == 'csv':
        import csv
        from django.http import HttpResponse
        r=HttpResponse(content_type='text/csv; charset=utf-8')
        r['Content-Disposition']='attachment; filename="course-progress.csv"'
        r.write('\ufeff')
        writer=csv.writer(r,delimiter=';')
        writer.writerow(['Ученик','Пройдено шагов','Всего','Баллы'])
        for row in rows:
            name=row['student'].username
            writer.writerow(["'"+name if name.startswith(('=','+','-','@','\t','\r')) else name,row['done'],row['total'],row['points']])
        return r
    return render(request,'studio/report.html',{'course':course,'rows':rows,'nav':'teacher'})
