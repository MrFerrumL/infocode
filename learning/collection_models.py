"""Task collections: links to external collections (Yandex Uchebnik) and the platform's own sets."""
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Collection(models.Model):
    SOURCES = [('infocode', 'Инфокод'), ('yandex', 'Яндекс Учебник')]
    key = models.CharField(max_length=80, unique=True, editable=False, help_text='Stable identity for repeatable imports.')
    source = models.CharField('Источник', max_length=12, choices=SOURCES, default='infocode')
    number = models.PositiveSmallIntegerField('Номер ЕГЭ', validators=[MinValueValidator(1), MaxValueValidator(27)])
    title = models.CharField('Название', max_length=160)
    level = models.CharField('Уровень / тема', max_length=120, blank=True)
    description = models.CharField('Описание', max_length=300, blank=True)
    url = models.URLField('Ссылка на подборку', max_length=500, blank=True, help_text='Для внешней подборки: открывается на её сайте.')
    tasks = models.ManyToManyField('learning.Task', through='CollectionItem', blank=True, related_name='collections')
    sort_order = models.PositiveIntegerField(default=0)
    published = models.BooleanField('Опубликована', default=True)

    class Meta:
        ordering = ['number', 'sort_order', 'pk']
        verbose_name = 'подборка'
        verbose_name_plural = 'подборки'

    def __str__(self):
        return self.title

    @property
    def number_label(self):
        return '19–21' if self.number == 19 else str(self.number)


class CollectionItem(models.Model):
    collection = models.ForeignKey(Collection, on_delete=models.CASCADE, related_name='items')
    task = models.ForeignKey('learning.Task', on_delete=models.CASCADE)
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ['position']
        constraints = [models.UniqueConstraint(fields=['collection', 'task'], name='unique_collection_task')]
