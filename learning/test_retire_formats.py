from io import StringIO
from django.core.management import call_command
from django.test import TestCase
from .models import Task, Topic, TaskVector


class RetireOldFormatsTests(TestCase):
    """Only the ЕГЭ-2027 formats stay in the catalogue: № 12 — Turing machine, № 23 — weighted graph in a file."""

    def setUp(self):
        t12, t23 = Topic.objects.create(number=12, title='12'), Topic.objects.create(number=23, title='23')
        self.t13 = Topic.objects.create(number=13, title='13')
        make = lambda topic, text, **kw: Task.objects.create(topic=topic, title='t', statement=text, answer='1',
                                                             published=True, in_catalog=True, **kw)
        self.turing = make(t12, 'Исполнитель МТ представляет собой головку, движущуюся вдоль бесконечной ленты.')
        self.editor = make(t12, 'Исполнитель Редактор: заменить(111, 2) пока нашлось(111).', tags='строки')
        self.picture = make(t12, '')  # text unknown: kept and listed
        self.graph = make(t23, 'В файле описание ациклического ориентированного взвешенного графа.')
        self.programs = make(t23, 'Исполнитель преобразует число: прибавить 1, умножить на 2. Сколько программ?')
        self.programs.title = 'Инфокод · № 23 · Сколько программ'
        self.programs.save()
        self.logic = make(t23, 'Сколько существует наборов значений логических переменных, удовлетворяющих системе уравнений?')
        self.from_source = make(t23, '', source_system='polyakov', external_id=5)
        TaskVector.objects.create(task=self.from_source, source_text='Исполнитель Удвоитель: сколько существует программ, переводящих 3 в 20?')

    def run_command(self, *args):
        out = StringIO()
        call_command('retire_old_formats', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_hide_and_restore(self):
        report = self.run_command('--dry-run')
        self.assertIn('№ 12: new format «машина Тьюринга» kept 1; old format hidden 1; without text, kept for a look: 1', report)
        self.assertIn('№ 23: new format «ациклический взвешенный граф в файле» kept 1; moved to № 13: 2; old format hidden 1', report)
        self.assertEqual(Task.objects.filter(in_catalog=False).count(), 0)
        self.run_command()
        hidden = set(Task.objects.filter(in_catalog=False).values_list('pk', flat=True))
        self.assertEqual(hidden, {self.editor.pk, self.logic.pk})
        # Counting executor programs is № 13 since ЕГЭ-2027: moved there, still in the catalogue.
        self.programs.refresh_from_db()
        self.assertEqual((self.programs.topic, self.programs.in_catalog, self.programs.title), (self.t13, True, 'Инфокод · № 13 · Сколько программ'))
        self.assertEqual(Task.objects.get(pk=self.from_source.pk).topic, self.t13)
        self.editor.refresh_from_db()
        self.assertEqual((self.editor.published, self.editor.tags), (True, 'строки, старый-формат'))
        self.assertIn('Back in the catalogue: 2', self.run_command('--restore'))
        self.editor.refresh_from_db()
        self.assertEqual((self.editor.in_catalog, self.editor.tags), (True, 'строки'))
