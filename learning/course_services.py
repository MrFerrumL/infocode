from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q, F
from .models import Course, Section, Lesson, Step, StepProgress, LessonProgress, Enrollment, StudentAssignment


def editable_courses(user):
    return Course.objects.all() if user.is_superuser else Course.objects.filter(owner=user)


def available_courses(user):
    public = Q(published=True) & (Q(group=None) | Q(group__students=user))
    if user.is_superuser:
        return Course.objects.all()
    return Course.objects.filter(public | Q(owner=user)).distinct()


def can_edit(user, course):
    return user.is_staff and (user.is_superuser or course.owner_id == user.id)


def published_steps(course):
    return Step.objects.filter(lesson__section__course=course, lesson__published=True, published=True).order_by('lesson__section__order', 'lesson__section_id', 'lesson__order', 'lesson_id', 'position', 'id')


def current_progress(user):
    return StepProgress.objects.filter(user=user, completed=True, revision=F('step__revision'), step__archived_at=None, step__lesson__archived_at=None, step__lesson__section__archived_at=None, step__lesson__section__course__archived_at=None)


def sync_lesson_progress(user, lesson):
    ids = list(lesson.steps.filter(published=True).values_list('id', flat=True))
    progress = current_progress(user).filter(step_id__in=ids)
    LessonProgress.objects.update_or_create(user=user, lesson=lesson, defaults={'passed': bool(ids) and progress.count() == len(ids), 'score': sum(progress.values_list('points', flat=True))})


@transaction.atomic
def bootstrap_course():
    """Add a course/steps to legacy lessons without changing existing material."""
    legacy_sections = Section.objects.filter(course=None)
    if legacy_sections.exists():
        owner = get_user_model().objects.filter(is_superuser=True).order_by('id').first()
        course, _ = Course.objects.get_or_create(title='ЕГЭ по информатике · от основ к практике', defaults={'owner': owner, 'published': True, 'description': 'Кодирование, логика, Python и алгоритмы. Короткие шаги, практика и понятные объяснения.'})
        legacy_sections.update(course=course)
    for lesson in Lesson.objects.filter(steps__isnull=True).distinct():
        position = 0
        if lesson.video:
            position += 1
            Step.objects.create(lesson=lesson, position=position, title='Видеоурок', kind='video', video=lesson.video.name, points=0)
        if lesson.theory:
            position += 1
            Step.objects.create(lesson=lesson, position=position, title=lesson.title, kind='text', body=lesson.theory, points=0)
        for q in lesson.questions.all():
            position += 1
            Step.objects.create(lesson=lesson, position=position, title='Проверь себя', kind='quiz', body=q.text, options=q.options, correct_choices=[q.correct_index], explanation=q.explanation)
        for f in lesson.files.all():
            position += 1
            Step.objects.create(lesson=lesson, position=position, title=f.title, kind='file', file=f.file.name, points=0)
        for old in LessonProgress.objects.filter(lesson=lesson, passed=True):
            for step in lesson.steps.all():
                StepProgress.objects.get_or_create(user=old.user, step=step, defaults={'completed': True, 'points': step.points if step.kind in ('quiz', 'answer') else 0})
            Enrollment.objects.get_or_create(user=old.user, course=lesson.section.course)
