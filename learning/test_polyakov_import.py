import hashlib
import io
import json
from pathlib import Path
import tempfile
import zipfile
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from PIL import Image
from learning.models import Task, TaskBlock


class PolyakovImportTests(TestCase):
    """Tasks cut from K. Polyakov's DOC files are imported as the 'К. Поляков' site."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        override = self.settings(MEDIA_ROOT=self.root / 'media')
        override.enable()
        self.addCleanup(override.disable)
        image = io.BytesIO()
        Image.new('RGB', (1920, 300), 'white').save(image, 'JPEG')
        assets = {'screenshots/17-472-condition.jpg': image.getvalue(), 'attachments/17-472/17-472.txt': b'1\n2\n3\n'}
        digest = lambda path: hashlib.sha256(assets[path]).hexdigest()
        row = {'number': 17, 'source_id': 472, 'source_url': 'https://kpolyakov.spb.ru/school/ege.htm', 'author': 'ЕГЭ-2026',
               'label': 'ЕГЭ-2026', 'kind': 'exam', 'answer': '33 164160', 'answer_origin': 'answers.xls', 'difficulty': 'medium',
               'solution': 'Видеоразбор: https://youtu.be/example',
               'images': [{'path': 'screenshots/17-472-condition.jpg', 'sha256': digest('screenshots/17-472-condition.jpg')}],
               'files': [{'path': 'attachments/17-472/17-472.txt', 'name': '17-472.txt', 'sha256': digest('attachments/17-472/17-472.txt')}]}
        manifest = {'format': 'infocode-polyakov-doc-v1', 'source': 'https://kpolyakov.spb.ru', 'numbers': [17],
                    'source_counts': {'17': 1}, 'source_sha256': {'17': 'd' * 64}, 'tasks': [row]}
        self.bundle = self.root / 'polyakov.zip'
        with zipfile.ZipFile(self.bundle, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
            for path, data in assets.items():
                archive.writestr(path, data)

    def run_import(self):
        out = io.StringIO()
        call_command('import_kompege_numbers', str(self.bundle), numbers=[17], publish=True, stdout=out)
        return out.getvalue()

    def test_task_is_labelled_as_polyakov_and_keeps_files_and_video(self):
        self.assertIn('created 1', self.run_import())
        task = Task.objects.get()
        self.assertEqual((task.source_system, task.external_id, task.title), ('polyakov', 472, 'К. Поляков 17.472'))
        self.assertEqual((task.source_label, task.source_kind, task.answer), ('ЕГЭ-2026', 'exam', '33 164160'))
        self.assertIn('youtu.be', task.solution)
        image = TaskBlock.objects.get(kind='image')
        self.assertTrue(image.image.name.startswith('imports/polyakov/472/'))
        self.assertEqual(TaskBlock.objects.get(kind='file').text, '17-472.txt')
        self.assertIn('skipped 1', self.run_import())

    def test_catalogue_site_filter_lists_polyakov(self):
        self.run_import()
        self.client.force_login(get_user_model().objects.create_user('pupil'))
        page = self.client.get('/catalog/', {'site': 'polyakov'})
        self.assertContains(page, 'Найдено: 1')
        self.assertContains(page, '<option value="polyakov"')
        self.assertContains(page, 'Сайт К. Полякова · задача 17.472')


class PolyakovGamesAndPartsTests(TestCase):
    """Polyakov's 19–21 come as groups (condition + the section's question); extra documents keep his numbering."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        override = self.settings(MEDIA_ROOT=self.root / 'media')
        override.enable()
        self.addCleanup(override.disable)

    def bundle(self, name, manifest, assets):
        path = self.root / name
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
            for asset, data in assets.items():
                archive.writestr(asset, data)
        return path

    def picture(self, colour):
        out = io.BytesIO()
        Image.new('RGB', (1920, 200), colour).save(out, 'JPEG')
        return out.getvalue()

    def row(self, number, ident, answer, images):
        return {'number': number, 'source_id': ident, 'source_url': 'https://kpolyakov.spb.ru/school/ege.htm', 'author': '',
                'label': 'Авторская · К. Поляков', 'kind': 'author', 'answer': answer, 'difficulty': 'medium', 'images': images, 'files': []}

    def test_game_groups_and_numbering(self):
        assets = {'screenshots/1921-4-condition.jpg': self.picture('white')}
        assets.update({f'screenshots/1921-s1-q{q}.jpg': self.picture(c) for q, c in ((19, 'red'), (20, 'green'), (21, 'blue'))})
        ref = lambda path: {'path': path, 'sha256': hashlib.sha256(assets[path]).hexdigest()}
        rows = [self.row(q, 4, str(q), [ref('screenshots/1921-4-condition.jpg'), ref(f'screenshots/1921-s1-q{q}.jpg')]) for q in (19, 20, 21)]
        games = self.bundle('games.zip', {'format': 'infocode-polyakov-games-v1', 'source': 'https://kpolyakov.spb.ru', 'numbers': [19, 20, 21],
                                          'source_counts': {'19': 1, '20': 1, '21': 1}, 'source_sha256': {str(n): 'e' * 64 for n in (19, 20, 21)}, 'tasks': rows}, assets)
        call_command('import_kompege_numbers', str(games), numbers=[19, 20, 21], publish=True, stdout=io.StringIO())
        self.assertEqual(sorted(Task.objects.values_list('title', flat=True)), ['К. Поляков 19-21.4'] * 3)
        parts = {'screenshots/7-2-15-condition.jpg': self.picture('white')}
        part = self.bundle('parts.zip', {'format': 'infocode-polyakov-doc-v1', 'source': 'https://kpolyakov.spb.ru', 'numbers': [7],
                                         'source_counts': {'7': 1}, 'source_sha256': {'7': 'f' * 64},
                                         'tasks': [self.row(7, 2015, '128', [{'path': 'screenshots/7-2-15-condition.jpg', 'sha256': hashlib.sha256(parts['screenshots/7-2-15-condition.jpg']).hexdigest()}])]}, parts)
        call_command('import_kompege_numbers', str(part), numbers=[7], publish=True, stdout=io.StringIO())
        self.assertTrue(Task.objects.filter(title='К. Поляков 7-2.15').exists())
        self.client.force_login(get_user_model().objects.create_user('pupil'))
        page = self.client.get('/catalog/', {'number': 19, 'site': 'polyakov'})
        self.assertContains(page, 'Найдено: 1')  # one card for the 19–21 group
        self.assertContains(page, 'задача 19-21.4')
