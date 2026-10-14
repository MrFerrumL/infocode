import json
from datetime import date, datetime, time, timedelta, timezone as utc_timezone
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from .models import ScheduleEvent, ScheduleOccurrence, AuditEvent
from .schedule_forms import ScheduleForm
from .schedule_services import (OCCURRENCE_FIELDS, editable_events, visible_events, occurrence,
                                expanded_events, assignment_events, notify_event, send_reminders)
from .security import claim_submission, form_token
from .views import staff_required


def parse_day(value):
    try:
        day = date.fromisoformat(value)
        if not 1900 <= day.year <= 9997:
            raise ValueError
        return day
    except (ValueError, TypeError):
        raise Http404('Укажите дату в формате ГГГГ-ММ-ДД.')


def range_for(request, maximum=62):
    begin = parse_day(request.GET.get('start', timezone.localdate().isoformat()))
    if not 1900 <= begin.year <= 9997:
        raise Http404('Дата вне диапазона календаря.')
    end = parse_day(request.GET.get('end', (begin + timedelta(days=31)).isoformat()))
    if not 0 < (end - begin).days <= maximum or begin.year < 1900 or end.year > 9998:
        raise Http404('Выберите более короткий период календаря.')
    return begin, end


def selected_occurrence(event, request):
    day = parse_day(request.GET.get('occurrence') or request.POST.get('occurrence') or timezone.localtime(event.starts_at).date().isoformat())
    item = occurrence(event, day)
    if not item:
        raise Http404
    return item


@login_required
@require_GET
def calendar(request):
    teacher = request.path.startswith('/teacher/')
    if teacher and not request.user.is_staff:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    send_reminders(user=request.user)
    return render(request, 'schedule/calendar.html', {
        'teacher': teacher, 'form': ScheduleForm(user=request.user) if teacher else None,
        'submit_token': form_token(request.user, 'schedule-new') if teacher else '',
        'nav': 'schedule', 'workspace_section': 'schedule',
    })


def serialized_events(user, begin, end):
    rows = []
    for item in expanded_events(visible_events(user), begin, end):
        event, day = item['event'], item['original_date'].isoformat()
        url = reverse('schedule_detail', args=[event.pk]) + '?occurrence=' + day
        rows.append({'id': f'event-{event.pk}-{day}', 'pk': event.pk, 'original_date': day,
            'title': item['title'], 'start': timezone.localtime(item['starts_at']).isoformat(),
            'end': timezone.localtime(item['ends_at']).isoformat(), 'all_day': item['all_day'],
            'color': item['color'], 'description': item['description'], 'location': item['location'],
            'meeting_url': item['meeting_url'], 'url': url, 'kind': 'lesson',
            'teacher': event.teacher.get_full_name() or event.teacher.username,
            'audience': event.group.title if event.group_id else event.get_audience_display(),
            'revision': event.revision, 'editable': user.is_staff and (event.teacher_id == user.pk or user.is_superuser),
            'edit_url': reverse('schedule_edit', args=[event.pk]) + '?occurrence=' + day,
            'move_url': reverse('schedule_move', args=[event.pk])})
    for work in assignment_events(user, begin, end):
        rows.append({'id': f'work-{work.pk}', 'title': 'Сдать: ' + work.title,
            'start': timezone.localtime(work.due_at).isoformat(),
            'end': timezone.localtime(work.due_at + timedelta(minutes=15)).isoformat(),
            'all_day': False, 'color': 'orange' if work.kind == 'homework' else 'pink',
            'kind': work.kind, 'description': work.description, 'location': '', 'meeting_url': '',
            'url': reverse('assignment_detail', args=[work.pk]), 'editable': False,
            'teacher': work.teacher.get_full_name() or work.teacher.username,
            'audience': work.get_kind_display()})
    return sorted(rows, key=lambda r: (r['start'], r['title']))


@login_required
@require_GET
def events(request):
    begin, end = range_for(request)
    send_reminders(user=request.user)
    rows = serialized_events(request.user, begin, end)
    if len(rows) > 5000:
        return JsonResponse({'error': 'Слишком много событий. Выберите более короткий период.'}, status=400)
    response = JsonResponse({'events': rows, 'timezone': 'Europe/Moscow'})
    response['Cache-Control'] = 'private, no-store'
    return response


def event_initial(request):
    initial = {}
    field = ScheduleForm.base_fields['starts_at']
    try:
        start = field.clean(request.GET.get('start', ''))
        if start:
            initial.update(starts_at=start, ends_at=start + timedelta(hours=1))
        if request.GET.get('end'):
            initial['ends_at'] = field.clean(request.GET['end'])
    except ValidationError:
        pass
    if request.GET.get('all_day') == '1':
        initial['all_day'] = True
    return initial


def log_event(user, event, action, day=None):
    AuditEvent.objects.create(user=user, action=action, model='ScheduleEvent', object_id=event.pk,
        title=event.title, changes={'revision': event.revision, 'occurrence': str(day or '')})


@staff_required
@transaction.atomic
def create(request):
    form = ScheduleForm(request.POST if request.method == 'POST' else None, user=request.user, initial=event_initial(request))
    if request.method == 'POST' and form.is_valid():
        if not claim_submission(request, 'schedule-new'):
            return redirect('teacher_schedule')
        event = form.save()
        notify_event(event, 'Добавлено занятие в расписание')
        log_event(request.user, event, 'schedule-create')
        messages.success(request, 'Занятие добавлено. Участники увидят его в своём расписании.')
        return redirect('teacher_schedule')
    return render(request, 'schedule/form.html', {'form': form, 'submit_token': form_token(request.user, 'schedule-new'),
        'nav': 'schedule', 'workspace_section': 'schedule'})


@staff_required
@transaction.atomic
def edit(request, pk):
    event = get_object_or_404(editable_events(request.user).select_for_update(), pk=pk)
    item = selected_occurrence(event, request)
    single = event.recurrence != 'none' and (request.POST.get('scope', 'single') if request.method == 'POST' else request.GET.get('scope', 'single')) != 'series'
    if single:
        instance = ScheduleEvent(**{field: item[field] for field in OCCURRENCE_FIELDS},
            teacher=event.teacher, audience=event.audience, group=event.group, assignment=event.assignment,
            reminder_minutes=event.reminder_minutes)
        initial = {'students': list(event.students.values_list('pk', flat=True))}
    else:
        instance, initial = event, {}
    form = ScheduleForm(request.POST if request.method == 'POST' else None, instance=instance,
        user=event.teacher, event=event, single=single, initial=initial)
    if request.method == 'POST' and form.is_valid():
        if single:
            ScheduleOccurrence.objects.update_or_create(event=event, original_date=item['original_date'],
                defaults={field: form.cleaned_data[field] for field in OCCURRENCE_FIELDS} | {'cancelled': False})
            event.revision += 1
            event.save(update_fields=['revision', 'updated_at'])
        else:
            # Series edits keep existing individually moved/cancelled occurrences.
            event = form.save(commit=False)
            event.revision += 1
            event.save()
            form.save_m2m()
        notify_event(event, 'Расписание изменилось', item['original_date'] if single else None)
        log_event(request.user, event, 'schedule-update', item['original_date'] if single else None)
        messages.success(request, 'Сохранено: одно занятие.' if single else 'Сохранены настройки события / серии.')
        return redirect('teacher_schedule')
    return render(request, 'schedule/form.html', {'form': form, 'event': event, 'single': single,
        'occurrence_date': item['original_date'], 'nav': 'schedule', 'workspace_section': 'schedule'})


@login_required
@require_GET
def detail(request, pk):
    event = get_object_or_404(visible_events(request.user).select_related('teacher', 'group', 'assignment'), pk=pk)
    item = selected_occurrence(event, request)
    teacher = request.user.is_staff and (event.teacher_id == request.user.pk or request.user.is_superuser)
    from .models import AssignmentRecipient
    work = event.assignment if event.assignment_id and (teacher or AssignmentRecipient.objects.filter(assignment=event.assignment, student=request.user).exists()) else None
    return render(request, 'schedule/detail.html', {'event': event, 'item': item, 'teacher': teacher, 'work': work,
        'nav': 'schedule', 'workspace_section': 'schedule'})


@staff_required
@require_POST
@transaction.atomic
def cancel(request, pk):
    event = get_object_or_404(editable_events(request.user).select_for_update(), pk=pk)
    if request.POST.get('revision') != str(event.revision):
        return HttpResponse('Событие уже изменилось. Обновите страницу.', status=409)
    item = selected_occurrence(event, request)
    single = event.recurrence != 'none' and request.POST.get('scope') != 'series'
    if single:
        ScheduleOccurrence.objects.update_or_create(event=event, original_date=item['original_date'],
            defaults={field: item[field] for field in OCCURRENCE_FIELDS} | {'cancelled': True})
    else:
        event.cancelled = True
    event.revision += 1
    event.save(update_fields=['cancelled', 'revision', 'updated_at'])
    notify_event(event, 'Занятие отменено', item['original_date'])
    log_event(request.user, event, 'schedule-cancel', item['original_date'] if single else None)
    messages.success(request, 'Занятие отменено.' if single else 'Событие / серия отменены.')
    return redirect('teacher_schedule')


@staff_required
@require_POST
@transaction.atomic
def move(request, pk):
    event = get_object_or_404(editable_events(request.user).select_for_update(), pk=pk)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            raise ValueError
        day = parse_day(data.get('original_date'))
        item = occurrence(event, day)
        if not item or item['cancelled']:
            raise ValueError
        if str(data.get('revision')) != str(event.revision):
            return JsonResponse({'error': 'Событие изменено в другой вкладке. Календарь обновлён.'}, status=409)
        start = ScheduleForm.base_fields['starts_at'].clean(data.get('start'))
        end = ScheduleForm.base_fields['ends_at'].clean(data.get('end'))
        proposal = ScheduleEvent(teacher=event.teacher, **{field: item[field] for field in OCCURRENCE_FIELDS})
        proposal.starts_at, proposal.ends_at = start, end
        if item['all_day'] and (start.time() != time.min or end.time() != time.min):
            raise ValidationError('Для события на весь день выберите границы дней.')
        proposal.clean()
    except (ValueError, TypeError, ValidationError, Http404, UnicodeDecodeError):
        return JsonResponse({'error': 'Некорректное время события.'}, status=400)
    if event.recurrence == 'none':
        event.starts_at, event.ends_at = start, end
    else:
        ScheduleOccurrence.objects.update_or_create(event=event, original_date=day,
            defaults={field: getattr(proposal, field) for field in OCCURRENCE_FIELDS} | {'cancelled': False})
    event.revision += 1
    event.save()
    notify_event(event, 'Занятие перенесено', day if event.recurrence != 'none' else None)
    log_event(request.user, event, 'schedule-move', day)
    return JsonResponse({'ok': True})


def ics_escape(value):
    return str(value).replace('\\', '\\\\').replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def fold_line(line):
    parts, current = [], ''
    for char in line:
        if len((current + char).encode('utf-8')) > 75:
            parts.append(current)
            current = ' '
        current += char
    return '\r\n'.join(parts + [current])


@login_required
@require_GET
def export(request):
    begin, end = range_for(request, maximum=366)
    rows = serialized_events(request.user, begin, end)
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Infocode//Schedule//RU', 'CALSCALE:GREGORIAN', 'X-WR-CALNAME:Инфокод']
    stamp = timezone.now().astimezone(utc_timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    for row in rows:
        lines += ['BEGIN:VEVENT', 'UID:' + row['id'] + '@infocode', 'DTSTAMP:' + stamp, 'SUMMARY:' + ics_escape(row['title'])]
        if row['all_day']:
            lines += ['DTSTART;VALUE=DATE:' + row['start'][:10].replace('-', ''), 'DTEND;VALUE=DATE:' + row['end'][:10].replace('-', '')]
        else:
            for key, label in [('start', 'DTSTART'), ('end', 'DTEND')]:
                lines.append(label + ':' + datetime.fromisoformat(row[key]).astimezone(utc_timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        lines += ['DESCRIPTION:' + ics_escape(row['description']), 'LOCATION:' + ics_escape(row['location']), 'END:VEVENT']
    lines.append('END:VCALENDAR')
    response = HttpResponse('\r\n'.join(fold_line(line) for line in lines) + '\r\n', content_type='text/calendar; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="infocode-calendar.ics"'
    response['Cache-Control'] = 'private, no-store'
    return response
