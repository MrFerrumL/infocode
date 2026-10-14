from datetime import timedelta
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from .assignments import own_groups, recipients_for
from .models import ScheduleEvent, LearningAssignment
from .rich_text import safe_link


class ScheduleForm(forms.ModelForm):
    weekdays = forms.MultipleChoiceField(label='Дни недели', required=False,
        choices=list(enumerate(['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'])), widget=forms.CheckboxSelectMultiple)
    reminder_minutes = forms.TypedChoiceField(label='Напоминание в платформе', coerce=int,
        choices=[(0, 'Без напоминания'), (5, 'За 5 минут'), (10, 'За 10 минут'), (15, 'За 15 минут'),
                 (30, 'За 30 минут'), (60, 'За час'), (1440, 'За сутки')])
    revision = forms.IntegerField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = ScheduleEvent
        fields = ['title', 'starts_at', 'ends_at', 'all_day', 'audience', 'group', 'students',
                  'recurrence', 'weekdays', 'repeat_until', 'reminder_minutes', 'color',
                  'location', 'meeting_url', 'assignment', 'description']
        widgets = {
            'starts_at': forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}),
            'ends_at': forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}),
            'repeat_until': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'students': forms.CheckboxSelectMultiple,
            'description': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, user, event=None, single=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = user
        self.event = event
        self.single = single
        self.instance.teacher = user
        self.fields['group'].queryset = own_groups(user).filter(teacher=user)
        self.fields['students'].queryset = recipients_for(user).filter(is_active=True)
        self.fields['assignment'].queryset = LearningAssignment.objects.filter(teacher=user, active=True)
        self.fields['revision'].required = event is not None
        self.initial['revision'] = event.revision if event else 1
        now = timezone.localtime().replace(second=0, microsecond=0)
        self.initial.setdefault('starts_at', now)
        self.initial.setdefault('ends_at', now + timedelta(hours=1))
        self.initial.setdefault('reminder_minutes', 30)
        self.initial.setdefault('repeat_until', (now + timedelta(days=90)).date())
        if single:
            for name in ('audience', 'group', 'students', 'recurrence', 'weekdays', 'repeat_until', 'assignment', 'reminder_minutes'):
                self.fields[name].disabled = True
            self.initial['recurrence'] = 'none'

    def clean_weekdays(self):
        return sorted(set(int(day) for day in self.cleaned_data['weekdays']))

    def clean_meeting_url(self):
        value = self.cleaned_data.get('meeting_url', '')
        if value and not safe_link(value):
            raise ValidationError('Используйте обычную ссылку https:// или http:// без логина в адресе.')
        return value

    def clean(self):
        d = super().clean()
        if self.event and d.get('revision') != self.event.revision:
            raise ValidationError('Событие уже изменено в другой вкладке. Обновите страницу и повторите правку.')
        if d.get('all_day') and d.get('starts_at') and d.get('ends_at'):
            start = d['starts_at'].replace(hour=0, minute=0, second=0, microsecond=0)
            end = d['ends_at'].replace(hour=0, minute=0, second=0, microsecond=0)
            if end.date() == start.date():
                end += timedelta(days=1)
            d.update(starts_at=start, ends_at=end)
        group = d.get('group')
        students = d.get('students')
        ids = set(students.values_list('pk', flat=True)) if students is not None else set()
        if d.get('audience') == 'selected':
            if group:
                ids.update(group.students.filter(is_active=True).values_list('pk', flat=True))
            if not ids:
                self.add_error('students', 'Выберите группу или хотя бы одного ученика.')
            if not ids.issubset(set(recipients_for(self.owner).values_list('pk', flat=True))):
                self.add_error('group', 'Все участники группы должны быть закреплены за вами.')
        elif d.get('audience') in ('all', 'private'):
            d['group'] = None
            d['students'] = self.fields['students'].queryset.none()
        if d.get('recurrence') not in ('weekly', 'biweekly'):
            d['weekdays'] = []
        elif not d.get('weekdays') and d.get('starts_at'):
            d['weekdays'] = [timezone.localtime(d['starts_at']).weekday()]
        if d.get('recurrence') == 'none':
            d['repeat_until'] = None
        if not self.single and d.get('assignment') and d.get('audience') != 'private':
            if d.get('audience') == 'all':
                ids = set(recipients_for(self.owner).values_list('pk', flat=True))
            recipients = set(d['assignment'].recipients.values_list('student_id', flat=True))
            if not ids.issubset(recipients):
                self.add_error('assignment', 'Выберите работу, назначенную всем участникам этого события.')
        return d
