"""Bounded teacher dashboard queries and a single entry point for reviewing work."""
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone

from .course_services import editable_courses
from .models import AssignmentRecipient, ExamAttempt, HomeworkSubmission, LearningAssignment, Task
from .studio_views import teacher_students
from .views import staff_required


def submissions_for(user):
    return HomeworkSubmission.objects.filter(recipient__assignment__teacher=user).select_related(
        'recipient__student', 'recipient__assignment')


def code_attempts_for(user):
    # The queue follows assignment ownership and the result page's student scope.
    return ExamAttempt.objects.filter(
        variant__assignment__teacher=user, user__in=teacher_students(user), submitted_at__isnull=False,
    ).select_related('user', 'variant').annotate(
        code_count=Count('answers', filter=~Q(answers__python_code='')),
    ).filter(code_count__gt=0).order_by('-submitted_at', '-pk')


def home_context(user):
    pending = submissions_for(user).filter(status='pending')
    courses = editable_courses(user)
    works = LearningAssignment.objects.filter(teacher=user, active=True)
    overdue = AssignmentRecipient.objects.filter(
        assignment__in=works, assignment__due_at__lt=timezone.now(), completed_at=None,
        student__in=teacher_students(user),
    ).values('student_id', 'student__first_name', 'student__last_name', 'student__username').annotate(
        overdue_count=Count('pk')).order_by('-overdue_count', 'student__username')
    return {
        'pending_count': pending.count(), 'pending_submissions': pending.order_by('created_at', 'pk')[:5],
        'student_count': teacher_students(user).count(), 'course_count': courses.count(),
        'task_count': Task.objects.count(), 'draft_courses': courses.filter(published=False).order_by('-pk')[:4],
        'upcoming': works.filter(due_at__gte=timezone.now()).annotate(recipient_count=Count('recipients')).order_by('due_at')[:5],
        'overdue_students': overdue[:5],
    }


@staff_required
def review_queue(request):
    mode = request.GET.get('mode', 'pending')
    if mode not in {'pending', 'reviewed', 'python'}:
        mode = 'pending'
    submissions = submissions_for(request.user)
    rows = code_attempts_for(request.user) if mode == 'python' else (
        submissions.filter(status='pending').order_by('created_at', 'pk') if mode == 'pending'
        else submissions.exclude(status='pending').order_by('-reviewed_at', '-pk'))
    query = request.GET.get('q', '').strip()[:120]
    if query:
        prefix = 'user' if mode == 'python' else 'recipient__student'
        title = 'variant__title' if mode == 'python' else 'recipient__assignment__title'
        rows = rows.filter(Q(**{f'{prefix}__username__icontains': query}) |
                           Q(**{f'{prefix}__first_name__icontains': query}) |
                           Q(**{f'{prefix}__last_name__icontains': query}) |
                           Q(**{f'{title}__icontains': query}))
    return render(request, 'platform/review_queue.html', {
        'page': Paginator(rows, 25).get_page(request.GET.get('page')), 'mode': mode, 'query': query,
        'pending_count': submissions.filter(status='pending').count(), 'nav': 'teacher',
    })
