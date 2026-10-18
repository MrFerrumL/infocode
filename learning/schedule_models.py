"""Shared lessons, recurring occurrences and notification receipts."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class ScheduleEvent(models.Model):
    COLORS = [('violet', 'Фиолетовый'), ('blue', 'Синий'), ('green', 'Зелёный'),
              ('orange', 'Оранжевый'), ('pink', 'Розовый')]
    REPEATS = [('none', 'Не повторять'), ('daily', 'Каждый день'), ('weekly', 'Каждую неделю'),
               ('biweekly', 'Раз в две недели'), ('monthly', 'Каждый месяц')]
    AUDIENCES = [('selected', 'Группа и выбранные ученики'), ('all', 'Все мои ученики'),
                 ('private', 'Только я')]
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='schedule_events')
    title = models.CharField('Название', max_length=180)
    description = models.TextField('Описание', max_length=10000, blank=True)
    starts_at = models.DateTimeField('Начало')
    ends_at = models.DateTimeField('Окончание')
    all_day = models.BooleanField('Весь день', default=False)
    color = models.CharField('Цвет', max_length=12, choices=COLORS, default='violet')
    location = models.CharField('Место / кабинет', max_length=240, blank=True)
    meeting_url = models.URLField('Ссылка на видеовстречу', max_length=500, blank=True)
    audience = models.CharField('Кому видно', max_length=12, choices=AUDIENCES, default='selected')
    # Shown to every signed-in pupil; reminders still go only to the audience above.
    public = models.BooleanField('Видно всем ученикам платформы', default=False,
        help_text='Выключено — занятие видят только ваши ученики (как выбрано в «Кому видно»).')
    group = models.ForeignKey('learning.StudyGroup', on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Группа')
    students = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name='scheduled_lessons', verbose_name='Отдельные ученики')
    assignment = models.ForeignKey('learning.LearningAssignment', on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Связанное ДЗ / КР')
    recurrence = models.CharField('Повторение', max_length=12, choices=REPEATS, default='none')
    weekdays = models.JSONField(default=list, blank=True)
    repeat_until = models.DateField('Повторять по', null=True, blank=True)
    reminder_minutes = models.PositiveSmallIntegerField('Напоминание', default=30)
    cancelled = models.BooleanField('Отменено', default=False)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['starts_at', 'pk']
        indexes = [models.Index(fields=['teacher', 'starts_at']), models.Index(fields=['recurrence', 'repeat_until'])]

    def __str__(self):
        return self.title

    def clean(self):
        if self.starts_at and self.ends_at:
            if not (1900 <= self.starts_at.year <= 9997 and 1900 <= self.ends_at.year <= 9997):
                raise ValidationError({'starts_at': 'Дата вне диапазона календаря.'})
            seconds = (self.ends_at - self.starts_at).total_seconds()
            if seconds <= 0 or seconds > (31 if self.all_day else 7) * 86400:
                raise ValidationError({'ends_at': 'Окончание должно быть позже начала. Максимум — 7 дней (31 для события на весь день).'})
        if self.recurrence != 'none' and self.starts_at:
            first = timezone.localtime(self.starts_at).date()
            if not self.repeat_until or not 0 <= (self.repeat_until - first).days <= 729:
                raise ValidationError({'repeat_until': 'Укажите последний день повторения в пределах двух лет от начала.'})
        if not isinstance(self.weekdays, list) or any(type(d) is not int or d not in range(7) for d in self.weekdays):
            raise ValidationError({'weekdays': 'Выберите дни недели.'})
        if self.reminder_minutes not in (0, 5, 10, 15, 30, 60, 1440):
            raise ValidationError({'reminder_minutes': 'Выберите доступное время напоминания.'})


class ScheduleOccurrence(models.Model):
    event = models.ForeignKey(ScheduleEvent, on_delete=models.CASCADE, related_name='exceptions')
    original_date = models.DateField()
    title = models.CharField(max_length=180)
    description = models.TextField(max_length=10000, blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    all_day = models.BooleanField(default=False)
    color = models.CharField(max_length=12, choices=ScheduleEvent.COLORS, default='violet')
    location = models.CharField(max_length=240, blank=True)
    meeting_url = models.URLField(max_length=500, blank=True)
    cancelled = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['event', 'original_date'], name='unique_schedule_occurrence')]


class ScheduleReminder(models.Model):
    event = models.ForeignKey(ScheduleEvent, on_delete=models.CASCADE)
    original_date = models.DateField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    revision = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['event', 'original_date', 'user', 'revision'], name='unique_schedule_reminder')]
