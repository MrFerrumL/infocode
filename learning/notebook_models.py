"""Teaching notebooks: ordered rich-text and media cells; code is never executed."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from .models import ActiveManager, file_size, video_size
from .rich_text import sanitize_html


class Notebook(models.Model):
    objects = ActiveManager()
    all_objects = models.Manager()
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='authored_notebooks', verbose_name='Автор')
    title = models.CharField('Название конспекта', max_length=180)
    description = models.TextField('Краткое описание', blank=True, max_length=2000)
    group = models.ForeignKey('learning.StudyGroup', on_delete=models.PROTECT, null=True, blank=True, verbose_name='Доступ в разделе «Конспекты»', help_text='Без группы — все ученики. В курсе доступ определяется настройками курса.')
    published = models.BooleanField('Опубликовать для учеников', default=False)
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    revision = models.PositiveIntegerField(default=1, editable=False)
    edit_version = models.PositiveIntegerField(default=1, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-id']
        verbose_name = 'конспект'
        verbose_name_plural = 'конспекты'

    def clean(self):
        count = getattr(self, '_cell_count', None)
        if count is None:
            count = self.cells.count() if self.pk else 0
        if self.published and not count:
            raise ValidationError('Перед публикацией добавьте хотя бы одну ячейку.')

    def __str__(self):
        return self.title


class NotebookCell(models.Model):
    KINDS = [('text', 'Текст'), ('code', 'Python-код'), ('image', 'Изображение'), ('video', 'Видео'), ('file', 'Файл'), ('divider', 'Разделитель')]
    notebook = models.ForeignKey(Notebook, on_delete=models.CASCADE, related_name='cells')
    kind = models.CharField('Тип ячейки', choices=KINDS, max_length=12, default='text')
    position = models.PositiveIntegerField('Порядок', default=1)
    text = models.TextField('Текст / Python-код', blank=True)
    caption = models.CharField('Подпись', max_length=300, blank=True)
    image = models.ImageField('Изображение', upload_to='notebooks/images/', blank=True, validators=[file_size, FileExtensionValidator(['png', 'jpg', 'jpeg', 'webp'])])
    video = models.FileField('Видео', upload_to='notebooks/videos/', blank=True, validators=[video_size, FileExtensionValidator(['mp4', 'webm'])])
    file = models.FileField('Файл', upload_to='notebooks/files/', blank=True, validators=[file_size, FileExtensionValidator(['txt', 'csv', 'xlsx', 'pdf', 'zip', 'docx', 'py'])])

    class Meta:
        ordering = ['position', 'id']
        verbose_name = 'ячейка конспекта'
        verbose_name_plural = 'ячейки конспекта'

    def clean(self):
        from django.utils.html import strip_tags
        if self.kind == 'text':
            self.text = sanitize_html(self.text)
            if not strip_tags(self.text).strip():
                raise ValidationError({'text': 'Напишите текст ячейки.'})
        if self.kind == 'code' and not self.text.strip():
            raise ValidationError({'text': 'Добавьте Python-код.'})
        if self.kind in ('image', 'video', 'file') and not getattr(self, self.kind):
            raise ValidationError({self.kind: 'Загрузите материал или выберите его из медиатеки.'})

    def save(self, *args, **kwargs):
        if self.kind == 'text':
            self.text = sanitize_html(self.text)
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.get_kind_display()} · конспект {self.notebook_id}'
