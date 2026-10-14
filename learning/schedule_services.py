"""Calendar expansion uses local wall-clock times; access follows teacher ownership."""
from datetime import datetime, time, timedelta
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from .models import ScheduleEvent, ScheduleReminder, Notification, LearningAssignment
from .studio_views import teacher_students

OCCURRENCE_FIELDS = ('title', 'description', 'starts_at', 'ends_at', 'all_day', 'color', 'location', 'meeting_url')


def editable_events(user):
    if not user.is_staff:
        return ScheduleEvent.objects.none()
    return ScheduleEvent.objects.all() if user.is_superuser else ScheduleEvent.objects.filter(teacher=user)


def visible_events(user):
    if user.is_staff:
        return editable_events(user)
    # Removed students cannot retain access through a stale event recipient.
    return ScheduleEvent.objects.filter(teacher__assigned_students__student=user).filter(
        Q(audience='all') | Q(audience='selected', students=user) |
        Q(audience='selected', group__students=user)).distinct()


def event_students(event):
    students = teacher_students(event.teacher).filter(is_active=True)
    if event.audience == 'private':
        return students.none()
    if event.audience == 'selected':
        audience = Q(pk__in=event.students.values('pk'))
        if event.group_id:
            audience |= Q(studygroup=event.group_id)
        students = students.filter(audience)
    return students.distinct()


def occurrence_dates(event, begin, end):
    first = timezone.localtime(event.starts_at).date()
    last = event.repeat_until if event.recurrence != 'none' else first
    if not last:
        return
    day = max(first, begin)
    while day < end and day <= last:
        delta = (day - first).days
        selected = event.weekdays or [first.weekday()]
        matches = (day == first if event.recurrence == 'none' else
                   True if event.recurrence == 'daily' else
                   day.day == first.day if event.recurrence == 'monthly' else
                   day.weekday() in selected and
                   ((day - timedelta(days=day.weekday()) - (first - timedelta(days=first.weekday()))).days // 7) %
                   (2 if event.recurrence == 'biweekly' else 1) == 0)
        if matches or day == first:
            yield day
        day += timedelta(days=1)


def occurrence(event, original_date, exceptions=None):
    if not next(occurrence_dates(event, original_date, original_date + timedelta(days=1)), None):
        return None
    start, end = timezone.localtime(event.starts_at), timezone.localtime(event.ends_at)
    offset = original_date - start.date()
    values = {field: getattr(event, field) for field in OCCURRENCE_FIELDS}
    values.update(starts_at=start + offset, ends_at=end + offset, cancelled=event.cancelled)
    exceptions = exceptions if exceptions is not None else {e.original_date: e for e in event.exceptions.all()}
    override = exceptions.get(original_date)
    if override:
        values.update({field: getattr(override, field) for field in OCCURRENCE_FIELDS})
        values['cancelled'] = event.cancelled or override.cancelled
    values.update(event=event, original_date=original_date)
    return values


def expanded_events(queryset, begin, end):
    # Padding catches multi-day events beginning before the requested window.
    lower = timezone.make_aware(datetime.combine(begin, time.min))
    upper = timezone.make_aware(datetime.combine(end, time.min))
    relevant = (Q(recurrence='none', starts_at__lt=upper, ends_at__gt=lower) |
                (~Q(recurrence='none') & Q(starts_at__lt=upper, repeat_until__gte=begin - timedelta(days=31))) |
                Q(exceptions__starts_at__lt=upper, exceptions__ends_at__gt=lower, exceptions__cancelled=False))
    events = queryset.filter(cancelled=False).filter(relevant).distinct()
    for event in events.select_related('teacher', 'group', 'assignment').prefetch_related('exceptions'):
        exceptions = {item.original_date: item for item in event.exceptions.all()}
        dates = set(occurrence_dates(event, begin - timedelta(days=31), end))
        # An exception can be moved into this window from an unrelated month.
        dates.update(d for d, item in exceptions.items()
                     if timezone.localtime(item.starts_at).date() < end and timezone.localtime(item.ends_at).date() >= begin)
        for day in sorted(dates):
            item = occurrence(event, day, exceptions)
            if item and not item['cancelled'] and timezone.localtime(item['starts_at']).date() < end and timezone.localtime(item['ends_at']).date() >= begin:
                if item['ends_at'] > lower:
                    yield item


def notify_event(event, title, original_date=None):
    url = reverse('schedule_detail', args=[event.pk])
    if original_date:
        url += '?occurrence=' + original_date.isoformat()
    Notification.objects.bulk_create([Notification(user_id=pk, title=title[:200], text=event.title, url=url)
        for pk in event_students(event).values_list('pk', flat=True)])


def assignment_events(user, begin, end):
    lower = timezone.make_aware(datetime.combine(begin, time.min))
    upper = timezone.make_aware(datetime.combine(end, time.min))
    qs = LearningAssignment.objects.filter(active=True, due_at__gte=lower, due_at__lt=upper)
    if user.is_staff:
        if not user.is_superuser:
            qs = qs.filter(teacher=user)
    else:
        qs = qs.filter(recipients__student=user)
    return qs.select_related('teacher').distinct()


def send_reminders(now=None, user=None):
    now = now or timezone.now()
    begin = timezone.localtime(now).date()
    qs = visible_events(user) if user else ScheduleEvent.objects.all()
    count = 0
    for item in expanded_events(qs.filter(reminder_minutes__gt=0), begin, begin + timedelta(days=2)):
        event = item['event']
        minutes = (item['starts_at'] - now).total_seconds() / 60
        if not 0 <= minutes <= event.reminder_minutes:
            continue
        ids = set(event_students(event).values_list('pk', flat=True))
        if event.teacher.is_active:
            ids.add(event.teacher_id)
        if user:
            ids.intersection_update({user.pk})
        for pk in ids:
            with transaction.atomic():
                receipt, created = ScheduleReminder.objects.get_or_create(event=event, original_date=item['original_date'], user_id=pk, revision=event.revision)
                if created:
                    Notification.objects.create(user_id=pk, title=f'Скоро: {item["title"]}'[:200],
                        text=f'{timezone.localtime(item["starts_at"]):%d.%m.%Y в %H:%M} (МСК)',
                        url=reverse('schedule_detail', args=[event.pk]) + '?occurrence=' + item['original_date'].isoformat())
                    count += 1
    return count
