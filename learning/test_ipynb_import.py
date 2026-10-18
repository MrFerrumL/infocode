import base64
import io
import json
import tempfile
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from PIL import Image
from learning.ipynb_import import colab_blocks, import_notebook
from learning.notebook_models import Notebook


def png():
    out = io.BytesIO()
    Image.new('L', (60, 30), 200).save(out, 'PNG')
    return base64.b64encode(out.getvalue()).decode()


def notebook_bytes(extra=0):
    cells = [
        {'cell_type': 'markdown', 'source': ['# Задание 24\n', '**Как пользоваться**\n', '1. Запустите\n', '2. Решайте']},
        {'cell_type': 'markdown', 'source': f'### Задача 1\n\n![Задача 1](data:image/png;base64,{png()})\n\n<details><summary>Текст условия</summary>Найдите длину.</details>\n\n<script>alert(1)</script>\n\n| a | b |\n|---|---|\n| 1 | 2 |'},
        {'cell_type': 'code', 'source': "print(max([1, 2]))", 'outputs': [{'output_type': 'stream', 'name': 'stdout', 'text': ['2\n']}]},
        {'cell_type': 'code', 'source': '1 + 1', 'outputs': [{'output_type': 'execute_result', 'data': {'text/plain': '2'}}]},
    ] + [{'cell_type': 'markdown', 'source': f'Шаг {i}'} for i in range(extra)]
    return json.dumps({'nbformat': 4, 'nbformat_minor': 0, 'metadata': {}, 'cells': cells}).encode()


class IpynbImportTests(TestCase):
    """Colab notebooks become «Конспекты» that read like Colab: text, pictures, code and its output."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        override = self.settings(MEDIA_ROOT=Path(temp.name))
        override.enable()
        self.addCleanup(override.disable)
        self.teacher = get_user_model().objects.create_user('teacher', is_staff=True, is_superuser=True)
        self.pupil = get_user_model().objects.create_user('pupil')

    def test_cells_follow_colab(self):
        notebook, count = import_notebook(notebook_bytes(), self.teacher, publish=True)
        self.assertEqual(notebook.title, 'Задание 24')
        kinds = list(notebook.cells.values_list('kind', flat=True))
        self.assertEqual(kinds, ['text', 'text', 'image', 'text', 'code', 'code'])
        first = notebook.cells.get(position=1).text
        self.assertIn('<ol>', first)  # a list right after a line of text, as Colab shows it
        details = notebook.cells.get(position=4).text
        self.assertIn('<details><summary>Текст условия</summary>', details)
        self.assertIn('<table>', details)
        self.assertNotIn('script', details)
        self.assertEqual(list(notebook.cells.filter(kind='code').values_list('output', flat=True)), ['2', '2'])
        self.client.force_login(self.pupil)
        page = self.client.get(f'/notebooks/{notebook.pk}/')
        self.assertContains(page, 'class="notebook-output"')
        self.assertContains(page, 'print(max([1, 2]))')

    def test_reimport_replaces_cells_and_long_notebooks_get_a_settings_page(self):
        path = Path(tempfile.mkdtemp()) / 'ege24.ipynb'
        path.write_bytes(notebook_bytes())
        call_command('import_ipynb', str(path), '--publish', stdout=io.StringIO())
        path.write_bytes(notebook_bytes(extra=45))
        call_command('import_ipynb', str(path), '--publish', stdout=io.StringIO())
        notebook = Notebook.objects.get()
        self.assertEqual(notebook.cells.count(), 51)
        self.client.force_login(self.teacher)
        editor = self.client.get(f'/teacher/notebooks/{notebook.pk}/edit/')
        self.assertContains(editor, 'Обновить из Colab')
        self.assertNotContains(editor, 'data-content-blocks')

    def test_upload_page(self):
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get('/teacher/notebooks/'), 'Загрузить из Colab')
        reply = self.client.post('/teacher/notebooks/import/', {'file': SimpleUploadedFile('n.ipynb', notebook_bytes()), 'title': 'Мой конспект', 'published': 'on'})
        notebook = Notebook.objects.get(title='Мой конспект')
        self.assertRedirects(reply, f'/notebooks/{notebook.pk}/', fetch_redirect_response=False)
        bad = self.client.post('/teacher/notebooks/import/', {'file': SimpleUploadedFile('n.ipynb', b'not json')})
        self.assertContains(bad, 'Это не файл .ipynb')
        self.client.force_login(self.pupil)
        self.assertNotEqual(self.client.get('/teacher/notebooks/import/').status_code, 200)

    def test_colab_blocks_keep_fenced_code(self):
        self.assertEqual(colab_blocks('text\n- a\n```\nx\n- y\n```'), 'text\n\n- a\n```\nx\n- y\n```')
