"""A dedicated homework workspace reuses the assignment and review lifecycle."""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.shortcuts import render
from django.utils import timezone
from .assignments import own_groups, recipient_status
from .models import AssignmentRecipient, HomeworkSubmission, LearningAssignment


def state_key(recipient):
    if recipient.completed_at:
        return 'done'
    latest = next(iter(recipient.submissions.all()), None)
    if latest and latest.status in ('pending', 'returned'):
        return latest.status
    if not recipient.assignment.active:
        return 'closed'
    if timezone.now() >= recipient.assignment.due_at:
        return 'overdue'
    return 'upcoming' if timezone.now() < recipient.assignment.starts_at else 'todo'


@login_required
def index(request):
    teacher = request.path.startswith('/teacher/')
    if teacher and not request.user.is_staff:
        raise PermissionDenied
    query = request.GET.get('q', '').strip()[:120]
    status = request.GET.get('status', '')
    group = request.GET.get('group', '')
    now = timezone.now()
    if teacher:
        works = LearningAssignment.objects.filter(kind='homework')
        if not request.user.is_superuser:
            works = works.filter(teacher=request.user)
        works = works.select_related('group', 'teacher').annotate(
            recipient_count=Count('recipients', distinct=True),
            done_count=Count('recipients', filter=Q(recipients__completed_at__isnull=False), distinct=True),
            pending_count=Count('recipients__submissions', filter=Q(recipients__submissions__status='pending'), distinct=True))
        counts = {'total': works.count(), 'open': works.filter(active=True, due_at__gte=now).count(),
                  'overdue': works.filter(active=True, due_at__lt=now).exclude(recipient_count=0).filter(done_count__lt=F('recipient_count')).count(),
                  'pending': HomeworkSubmission.objects.filter(recipient__assignment__in=works, status='pending').count()}
        if query:
            works = works.filter(title__icontains=query)
        if group.isdigit():
            works = works.filter(group_id=group)
        if status == 'open':
            works = works.filter(active=True, due_at__gte=now)
        elif status == 'closed':
            works = works.filter(active=False)
        elif status == 'overdue':
            works = works.filter(active=True, due_at__lt=now, done_count__lt=F('recipient_count'))
        elif status == 'pending':
            works = works.filter(pending_count__gt=0)
        page = Paginator(works.order_by('-active', 'due_at', '-pk'), 18).get_page(request.GET.get('page'))
        rows = page
    else:
        recipients = AssignmentRecipient.objects.filter(student=request.user, assignment__kind='homework').select_related('assignment__teacher', 'assignment__group').prefetch_related('submissions')
        rows = []
        counts = {'total': 0, 'todo': 0, 'overdue': 0, 'done': 0}
        for r in recipients.order_by('completed_at', 'assignment__due_at'):
            r.state_key = state_key(r)
            r.state = recipient_status(r) if r.assignment.active or r.completed_at else 'Закрыто'
            counts['total'] += 1
            counts['todo'] += r.state_key in ('todo', 'upcoming', 'returned')
            counts['overdue'] += r.state_key == 'overdue'
            counts['done'] += r.state_key == 'done'
            if query and query.casefold() not in r.assignment.title.casefold():
                continue
            if status and status != r.state_key:
                continue
            rows.append(r)
        page = Paginator(rows, 18).get_page(request.GET.get('page'))
        rows = page
    return render(request, 'homework/index.html', {'teacher': teacher, 'rows': rows, 'page': page,
        'counts': counts, 'query': query, 'status': status, 'group': group,
        'groups': own_groups(request.user) if teacher else [], 'nav': 'homework', 'workspace_section': 'homework'})
