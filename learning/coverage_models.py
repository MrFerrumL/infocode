"""«Пройденные номера»: which exam numbers each group has covered in lessons, as in the teacher's sheet."""
from django.conf import settings
from django.db import models


class GroupTopicMark(models.Model):
    COLORS = [('', 'Без цвета'), ('yellow', 'Жёлтый — на повторение'), ('red', 'Красный — требует внимания')]
    group = models.ForeignKey('learning.StudyGroup', on_delete=models.CASCADE, related_name='topic_marks')
    column = models.PositiveSmallIntegerField('Номер (0 — введение)')
    done = models.BooleanField('Пройдено', default=False)
    color = models.CharField('Цвет', max_length=10, choices=COLORS, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['group', 'column'], name='unique_group_topic_mark')]
        verbose_name = 'отметка пройденного номера'
        verbose_name_plural = 'пройденные номера'
