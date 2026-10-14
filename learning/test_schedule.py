import io
import json
import tempfile
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import (AssignmentRecipient, HomeworkSubmission, LearningAssignment, Notification,
                     ScheduleEvent, ScheduleOccurrence, ScheduleReminder, StudentAssignment, StudyGroup)
from .schedule_services import event_students, expanded_events, occurrence_dates, send_reminders


def at(day='2026-10-19', hour=16):
    return timezone.make_aware(datetime.fromisoformat(f'{day}T{hour:02d}:00:00'))


class BaseScheduleCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user('calendar-teacher', is_staff=True, first_name='Преподаватель')
        cls.other_teacher = User.objects.create_user('calendar-other-teacher', is_staff=True)
        cls.student = User.objects.create_user('calendar-student', first_name='Аня')
        cls.second = User.objects.create_user('calendar-second')
        cls.foreign = User.objects.create_user('calendar-foreign')
        for student in (cls.student, cls.second):
            StudentAssignment.objects.create(teacher=cls.teacher, student=student)
        StudentAssignment.objects.create(teacher=cls.other_teacher, student=cls.foreign)
        cls.group = StudyGroup.objects.create(title='ЕГЭ по понедельникам', teacher=cls.teacher)
        cls.group.students.add(cls.student)
        cls.other_group = StudyGroup.objects.create(title='Чужая группа', teacher=cls.other_teacher)
        cls.other_group.students.add(cls.foreign)
        cls.event = ScheduleEvent.objects.create(teacher=cls.teacher, title='Алгоритмы', starts_at=at(), ends_at=at(hour=17),
            recurrence='weekly', weekdays=[0], repeat_until=date(2026, 11, 30), group=cls.group)

    def setUp(self):
        self.client.force_login(self.teacher)

    def data(self, **updates):
        values = {'title': 'Практика Python', 'starts_at': '2026-10-20T18:00', 'ends_at': '2026-10-20T19:30',
            'audience': 'selected', 'students': [self.student.pk], 'recurrence': 'none', 'reminder_minutes': 30,
            'color': 'blue', 'description': 'Решаем задания', 'revision': 1}
        values.update(updates)
        return values

    def feed(self, start='2026-10-01', end='2026-11-01'):
        response = self.client.get(reverse('schedule_events'), {'start': start, 'end': end})
        self.assertEqual(response.status_code, 200)
        return response.json()['events']

class ScheduleTests(BaseScheduleCase):
    def test_calendar_pages_and_editor_render_for_roles(self):
        for name in ('teacher_schedule', 'schedule_create'):
            self.assertContains(self.client.get(reverse(name)), 'МСК' if name == 'teacher_schedule' else 'Новое занятие')
        self.assertContains(self.client.get(reverse('schedule_edit', args=[self.event.pk])), 'Только это занятие')
        self.client.force_login(self.student)
        page = self.client.get(reverse('schedule'))
        self.assertContains(page, 'Расписание занятий')
        self.assertNotContains(page, 'data-calendar-create')
        self.assertEqual(self.client.get(reverse('teacher_schedule')).status_code, 403)

    def test_feed_and_detail_isolate_students_and_private_events(self):
        private = ScheduleEvent.objects.create(teacher=self.teacher, title='Личные дела', audience='private', starts_at=at(), ends_at=at(hour=17))
        foreign = ScheduleEvent.objects.create(teacher=self.other_teacher, title='Чужое занятие', audience='all', starts_at=at(), ends_at=at(hour=17))
        self.assertEqual({row['title'] for row in self.feed()}, {'Алгоритмы', 'Личные дела'})
        self.client.force_login(self.student)
        rows = self.feed()
        self.assertEqual({r['title'] for r in rows}, {'Алгоритмы'})
        self.assertTrue(all(not row['editable'] for row in rows))
        for event in (private, foreign):
            self.assertEqual(self.client.get(reverse('schedule_detail', args=[event.pk])).status_code, 404)
        self.client.force_login(self.second)
        self.assertEqual(self.feed(), [])

    def test_unassigned_student_loses_stale_recipient_access(self):
        self.event.students.add(self.student)
        StudentAssignment.objects.filter(teacher=self.teacher, student=self.student).delete()
        self.client.force_login(self.student)
        self.assertEqual(self.feed(), [])

    def test_selected_student_without_group_does_not_notify_others(self):
        event = ScheduleEvent.objects.create(teacher=self.teacher, title='Индивидуальное', starts_at=at(), ends_at=at(hour=17))
        event.students.add(self.student)
        self.assertEqual(list(event_students(event)), [self.student])

    def test_creation_validates_recipients_times_and_links(self):
        for updates in ({'students': [self.foreign.pk]}, {'group': self.other_group.pk},
                        {'ends_at': '2026-10-20T17:00'}, {'meeting_url': 'javascript:alert(1)'},
                        {'recurrence': 'daily', 'repeat_until': '2030-01-01'}):
            page = self.client.post(reverse('schedule_create'), self.data(**updates))
            self.assertEqual(page.status_code, 200)
            self.assertTrue(page.context['form'].errors)
        self.assertEqual(ScheduleEvent.objects.count(), 1)
        page = self.client.post(reverse('schedule_create'), self.data())
        self.assertRedirects(page, reverse('teacher_schedule'))
        event = ScheduleEvent.objects.latest('pk')
        self.assertEqual(event.teacher, self.teacher)
        self.assertEqual(list(event.students.all()), [self.student])
        self.assertEqual(Notification.objects.filter(user=self.student, title='Добавлено занятие в расписание').count(), 1)
        self.assertFalse(Notification.objects.filter(user=self.second).exists())

    def test_weekly_biweekly_monthly_and_multi_day_expansion(self):
        self.assertEqual(list(occurrence_dates(self.event, date(2026, 10, 19), date(2026, 11, 2))), [date(2026, 10, 19), date(2026, 10, 26)])
        self.event.recurrence = 'biweekly'
        self.event.weekdays = [0, 2]
        self.assertEqual(list(occurrence_dates(self.event, date(2026, 10, 19), date(2026, 11, 5))),
            [date(2026, 10, 19), date(2026, 10, 21), date(2026, 11, 2), date(2026, 11, 4)])
        self.event.recurrence = 'monthly'
        self.event.starts_at, self.event.ends_at = at('2026-01-31'), at('2026-01-31', 17)
        self.event.repeat_until = date(2026, 4, 30)
        self.assertEqual(list(occurrence_dates(self.event, date(2026, 1, 1), date(2026, 5, 1))), [date(2026, 1, 31), date(2026, 3, 31)])

    def test_edit_one_occurrence_keeps_series_and_protects_stale_revision(self):
        url = reverse('schedule_edit', args=[self.event.pk]) + '?occurrence=2026-10-26'
        page = self.client.post(url, self.data(scope='single', title='Перенесённое', starts_at='2026-10-27T18:00', ends_at='2026-10-27T19:00'))
        self.assertEqual(page.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, 'Алгоритмы')
        self.assertEqual(self.event.starts_at, at())
        self.assertEqual(self.event.revision, 2)
        override = ScheduleOccurrence.objects.get(event=self.event)
        self.assertEqual(override.original_date, date(2026, 10, 26))
        rows = self.feed('2026-10-26', '2026-10-28')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['start'][:16], '2026-10-27T18:00')
        stale = self.client.post(url, self.data(scope='single'))
        self.assertEqual(stale.status_code, 200)
        self.assertContains(stale, 'Событие уже изменено')

    def test_series_edit_retains_individual_cancellation(self):
        self.client.post(reverse('schedule_cancel', args=[self.event.pk]), {'revision': 1, 'scope': 'single', 'occurrence': '2026-10-26'})
        page = self.client.post(reverse('schedule_edit', args=[self.event.pk]), self.data(scope='series', title='Новая серия',
            starts_at='2026-10-19T18:00', ends_at='2026-10-19T19:00', recurrence='weekly', weekdays=[0], repeat_until='2026-11-30', revision=2))
        self.assertEqual(page.status_code, 302)
        rows = self.feed()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['title'], 'Новая серия')
        self.assertTrue(ScheduleOccurrence.objects.get(event=self.event).cancelled)

    def test_cancel_single_or_series_with_owner_and_revision_checks(self):
        url = reverse('schedule_cancel', args=[self.event.pk])
        self.client.force_login(self.other_teacher)
        self.assertEqual(self.client.post(url, {'revision': 1}).status_code, 404)
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, {'revision': 9}).status_code, 409)
        self.assertEqual(self.client.post(url, {'revision': 1, 'scope': 'single', 'occurrence': '2026-10-26'}).status_code, 302)
        self.assertEqual(len(self.feed()), 1)
        self.assertEqual(self.client.post(url, {'revision': 2, 'scope': 'series', 'occurrence': '2026-10-19'}).status_code, 302)
        self.assertEqual(self.feed(), [])

    def test_move_keeps_duration_and_finds_exception_outside_original_month(self):
        url = reverse('schedule_move', args=[self.event.pk])
        payload = {'original_date': '2026-10-19', 'revision': 1, 'start': '2026-12-05T10:00', 'end': '2026-12-05T11:00'}
        self.assertEqual(self.client.post(url, json.dumps(payload), content_type='application/json').status_code, 200)
        rows = self.feed('2026-12-01', '2026-12-10')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['original_date'], '2026-10-19')
        self.assertEqual(rows[0]['start'][:16], '2026-12-05T10:00')
        self.assertEqual(self.client.post(url, json.dumps(payload), content_type='application/json').status_code, 409)
        self.assertEqual(self.client.post(url, '[]', content_type='application/json').status_code, 400)

    def test_all_day_normalizes_midnight_and_blocks_invalid_drag(self):
        self.client.post(reverse('schedule_create'), self.data(all_day='on', starts_at='2026-10-20T18:00', ends_at='2026-10-20T19:30'))
        event = ScheduleEvent.objects.latest('pk')
        self.assertEqual(timezone.localtime(event.starts_at).hour, 0)
        self.assertEqual((event.ends_at-event.starts_at).days, 1)
        payload = {'original_date': '2026-10-20', 'revision': 1, 'start': '2026-10-21T08:00', 'end': '2026-10-22T08:00'}
        self.assertEqual(self.client.post(reverse('schedule_move', args=[event.pk]), json.dumps(payload), content_type='application/json').status_code, 400)

    def test_reminders_are_private_idempotent_and_ignore_cancelled_occurrences(self):
        now = self.event.starts_at - timedelta(minutes=15)
        self.assertEqual(send_reminders(now), 2)
        self.assertEqual(send_reminders(now), 0)
        self.assertEqual(set(ScheduleReminder.objects.values_list('user_id', flat=True)), {self.teacher.pk, self.student.pk})
        self.event.cancelled = True
        self.event.save()
        self.assertEqual(send_reminders(self.event.starts_at + timedelta(days=7) - timedelta(minutes=15)), 0)

    def test_ics_export_has_utf8_folding_and_private_cache(self):
        self.event.title = 'Название, с точкой; и длинный текст ' * 8
        self.event.save()
        self.client.force_login(self.student)
        page = self.client.get(reverse('schedule_export'), {'start': '2026-10-19', 'end': '2026-10-20'})
        self.assertEqual(page.status_code, 200)
        text = page.content.decode('utf-8')
        self.assertIn('BEGIN:VCALENDAR\r\n', text)
        self.assertIn('DTSTART:20261019T130000Z', text)
        self.assertTrue(all(len(line.encode('utf-8')) <= 75 for line in text.split('\r\n')))
        self.assertEqual(page['Cache-Control'], 'private, no-store')
        self.assertEqual(self.client.get(reverse('schedule_events'), {'start': '9999-12-31'}).status_code, 404)
        self.assertEqual(self.client.get(reverse('schedule_detail', args=[self.event.pk]), {'occurrence': '9999-12-31'}).status_code, 404)

    def test_mutations_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.teacher)
        self.assertEqual(client.post(reverse('schedule_create'), self.data()).status_code, 403)

    def test_portable_backup_contains_calendar_and_exception(self):
        ScheduleOccurrence.objects.create(event=self.event, original_date=date(2026, 10, 26), title='Перенос', starts_at=at('2026-10-27'), ends_at=at('2026-10-27', 17))
        with tempfile.TemporaryDirectory() as temp, override_settings(MEDIA_ROOT=Path(temp)/'uploads'):
            target = Path(temp)/'backup.zip'
            call_command('backup_platform', str(target), stdout=io.StringIO())
            with zipfile.ZipFile(target) as archive:
                data = json.loads(archive.read('data.json'))
            self.assertTrue(any(row['model'] == 'learning.scheduleevent' for row in data))
            self.assertTrue(any(row['model'] == 'learning.scheduleoccurrence' for row in data))


class HomeworkWorkspaceTests(BaseScheduleCase):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.work = LearningAssignment.objects.create(teacher=self.teacher, kind='homework', title='ДЗ по графам', target='manual', starts_at=now-timedelta(days=2), due_at=now-timedelta(days=1))
        self.recipient = AssignmentRecipient.objects.create(assignment=self.work, student=self.student)
        self.kr = LearningAssignment.objects.create(teacher=self.teacher, kind='required_test', title='Отдельная контрольная', starts_at=now, due_at=now+timedelta(days=1))
        AssignmentRecipient.objects.create(assignment=self.kr, student=self.student)

    def test_homework_pages_counts_and_filters_are_scoped(self):
        foreign = LearningAssignment.objects.create(teacher=self.other_teacher, kind='homework', title='Чужое ДЗ', target='manual', starts_at=timezone.now(), due_at=timezone.now()+timedelta(days=1))
        AssignmentRecipient.objects.create(assignment=foreign, student=self.foreign)
        page = self.client.get(reverse('teacher_homework'))
        self.assertContains(page, 'ДЗ по графам')
        self.assertNotContains(page, 'Чужое ДЗ')
        self.assertNotContains(page, 'Отдельная контрольная')
        self.assertEqual(page.context['counts']['overdue'], 1)
        self.client.force_login(self.student)
        page = self.client.get(reverse('homework'), {'status': 'overdue'})
        self.assertContains(page, 'ДЗ по графам')
        self.assertEqual(page.context['counts']['overdue'], 1)
        self.assertEqual(self.client.get(reverse('teacher_homework')).status_code, 403)
        self.client.force_login(self.second)
        self.assertNotContains(self.client.get(reverse('homework')), 'ДЗ по графам')

    def test_homework_creation_forces_kind_and_reuses_submission_lifecycle(self):
        now = timezone.localtime()
        page = self.client.post(reverse('homework_create'), {'title': 'Ручное ДЗ', 'kind': 'required_test', 'target': 'manual',
            'students': [self.student.pk], 'starts_at': (now-timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
            'due_at': (now+timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'), 'max_attempts': 2, 'pass_percent': 60})
        self.assertEqual(page.status_code, 302)
        work = LearningAssignment.objects.latest('pk')
        self.assertEqual(work.kind, 'homework')
        self.client.force_login(self.student)
        page = self.client.post(reverse('assignment_detail', args=[work.pk]), {'text': 'Моё решение'})
        self.assertEqual(page.status_code, 302)
        self.assertEqual(HomeworkSubmission.objects.filter(recipient__assignment=work).count(), 1)
        page = self.client.get(reverse('homework'), {'status': 'pending'})
        self.assertContains(page, 'Ручное ДЗ')

    def test_calendar_includes_only_visible_homework_and_test_deadlines(self):
        self.client.force_login(self.student)
        begin = timezone.localdate() - timedelta(days=3)
        end = timezone.localdate() + timedelta(days=3)
        rows = self.feed(begin.isoformat(), end.isoformat())
        titles = {row['title'] for row in rows}
        self.assertIn('Сдать: ДЗ по графам', titles)
        self.assertIn('Сдать: Отдельная контрольная', titles)
        self.client.force_login(self.second)
        self.assertFalse(any(row['kind'] in ('homework', 'required_test') for row in self.feed(begin.isoformat(), end.isoformat())))
