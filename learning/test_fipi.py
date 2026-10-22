import io
import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from PIL import Image
from learning.fipi_check import Unavailable
from learning.models import Practice, Task, TaskBlock, Topic
from learning.security import form_token
from learning.services import create_variant


def picture():
    out = io.BytesIO()
    Image.new('RGB', (40, 20), 'white').save(out, 'PNG')
    return out.getvalue()


class FipiTests(TestCase):
    """FIPI open-bank tasks: imported with their layout, answers checked by FIPI once and then remembered."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        override = self.settings(MEDIA_ROOT=self.root / 'media')
        override.enable()
        self.addCleanup(override.disable)
        rows = [{'id': '48F84F', 'external_id': 0x48F84F, 'guid': '0011FF4F34E082534C60E5C3716A2AB5', 'number': 13, 'kes': ['1.2'],
                 'blocks': [{'kind': 'html', 'text': '<p class=MsoNormal>Сеть задана адресом</p><table><tr><td>1</td></tr></table><script>x</script>'},
                            {'kind': 'image', 'path': 'images/48F84F/a.png'}, {'kind': 'file', 'path': 'files/48F84F/data.xlsx', 'name': 'data.xlsx'}]}]
        self.bundle = self.root / 'fipi.zip'
        with zipfile.ZipFile(self.bundle, 'w') as out:
            out.writestr('manifest.json', json.dumps({'format': 'infocode-fipi-bank-v2', 'project': 'B9AC', 'tasks': rows}))
            out.writestr('images/48F84F/a.png', picture())
            out.writestr('files/48F84F/data.xlsx', b'PK fake')
        call_command('import_fipi', str(self.bundle), stdout=io.StringIO())
        self.task = Task.objects.get(source_system='fipi')
        self.pupil = get_user_model().objects.create_user('pupil')
        self.client.force_login(self.pupil)

    def answer(self, value):
        return self.client.post('/catalog/', {'task_id': self.task.pk, 'submit_token': form_token(self.pupil, f'task:{self.task.pk}'),
                                f'task-{self.task.pk}-answer': value}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_import_keeps_layout(self):
        blocks = list(TaskBlock.objects.filter(task=self.task).values_list('kind', flat=True))
        self.assertEqual(blocks, ['html', 'image', 'file'])
        text = TaskBlock.objects.get(task=self.task, kind='html').text
        self.assertIn('<table>', text)
        self.assertNotIn('script', text)
        self.assertEqual((self.task.topic.number, self.task.answer, self.task.check_ref), (13, '', '0011FF4F34E082534C60E5C3716A2AB5'))
        page = self.client.get('/catalog/', {'site': 'fipi'})
        self.assertContains(page, 'Открытый банк ФИПИ')
        self.assertContains(page, 'rich-statement')

    def test_answers_are_checked_by_fipi_then_remembered(self):
        with patch('learning.fipi_check.check', return_value=False) as check:
            self.assertFalse(self.answer('1').json()['solved'])
            check.assert_called_once_with('0011FF4F34E082534C60E5C3716A2AB5', '1')
        with patch('learning.fipi_check.check', side_effect=Unavailable('down')):
            reply = self.answer('2').json()
            self.assertIn('ФИПИ сейчас не отвечает', str(reply['errors']))
        self.assertEqual(Practice.objects.count(), 1)  # an outage records nothing
        with patch('learning.fipi_check.check', return_value=True):
            self.assertTrue(self.answer(' 9883255254 ').json()['solved'])
        self.task.refresh_from_db()
        self.assertEqual(self.task.answer, '9883255254')
        with patch('learning.fipi_check.check') as check:  # now checked locally
            self.assertTrue(self.answer('9883255254').json()['solved'])
            check.assert_not_called()
        # Re-import keeps the learned answer.
        call_command('import_fipi', str(self.bundle), stdout=io.StringIO())
        self.task.refresh_from_db()
        self.assertEqual(self.task.answer, '9883255254')

    def test_variants_skip_tasks_with_unknown_answer(self):
        teacher = get_user_model().objects.create_user('teacher', is_staff=True)
        with self.assertRaises(ValidationError):
            create_variant(creator=teacher, title='v', topics=[], task_ids=str(self.task.pk))
        topic = Topic.objects.get(number=13)
        with self.assertRaises(ValidationError):
            create_variant(creator=teacher, title='v', topics=[topic], count=1)
