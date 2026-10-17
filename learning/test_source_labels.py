import io
import json
import tempfile
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from learning.models import Task, Topic
from learning.sources import author_in_condition, classify, label


class SourceLabelTests(TestCase):
    """Notes above tasks as on KOMPEGE, with type and site filters in the catalogue."""

    def setUp(self):
        topic = Topic.objects.create(number=8, title='Комбинаторика')
        make = lambda **kw: Task.objects.create(topic=topic, statement='x', answer='1', **kw)
        self.demo = make(title='demo', source_system='kompege', external_id=31505, source_label='Демоверсия 2027', source_kind='demo')
        self.exam = make(title='exam', source_system='kompege', external_id=31146, source_label='Основная волна 19.06.26', source_kind='exam')
        self.author = make(title='author', source_system='kompege', external_id=5000, source_label='Авторская', source_kind='author')
        self.own = make(title='own')
        self.client.force_login(get_user_model().objects.create_user('pupil'))

    def titles(self, **params):
        page = self.client.get('/catalog/', {'number': 8, **params})
        return sorted(task.title for task in page.context['page'])

    def test_classification_and_author_from_condition(self):
        self.assertEqual([classify(n) for n in ('Демоверсия 2027', 'Основная волна 18.06.26', 'Резервный день 19.06.25', 'Пересдача 08.07.26',
                                                'ЕГКР 18.04.26', 'Апробация 04.03.26', 'Статград 12.2024', 'Джобс 12.03.2025', '')],
                         ['demo', 'exam', 'exam', 'exam', 'probe', 'probe', 'probe', 'author', 'author'])
        self.assertEqual(author_in_condition('<p><a href="x"><strong>(Л. Шастин)</strong></a> Исполнитель'), 'Л. Шастин')
        self.assertEqual(label('', '<p>(М. Ишимов) Текст</p>'), 'Авторская · М. Ишимов')
        self.assertEqual(label('', '<p>Текст без автора</p>'), 'Авторская')

    def test_note_is_shown_above_the_task(self):
        page = self.client.get('/catalog/', {'number': 8})
        self.assertContains(page, '<span class="source-note kind-demo" title="Демоверсия">Демоверсия 2027</span>', html=True)

    def test_type_and_site_filters(self):
        self.assertEqual(self.titles(kind='demo'), ['demo'])
        self.assertEqual(self.titles(kind='exam'), ['exam'])
        self.assertEqual(self.titles(site='kompege'), ['author', 'demo', 'exam'])
        self.assertEqual(self.titles(site='own'), ['own'])
        self.assertEqual(self.titles(kind='bogus'), ['author', 'demo', 'exam', 'own'])
        page = self.client.get('/catalog/')
        self.assertContains(page, '<option value="kompege"')
        self.assertContains(page, '<option value="own"')
        self.assertNotContains(page, '<option value="yandex"')

    def test_command_sets_notes_from_list(self):
        notes = [{'system': 'kompege', 'topic': 8, 'id': 5000, 'label': 'Авторская · Л. Шастин', 'kind': 'author'}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'notes.json'
            path.write_text(json.dumps(notes, ensure_ascii=False), encoding='utf-8')
            out = io.StringIO()
            call_command('label_source_tasks', str(path), stdout=out)
        self.assertIn('Updated notes of 1 tasks', out.getvalue())
        self.author.refresh_from_db()
        self.assertEqual(self.author.source_label, 'Авторская · Л. Шастин')
