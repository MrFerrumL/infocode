from io import BytesIO, StringIO
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from PIL import Image
from . import embeddings, ocr, semantic_search
from .models import Task, TaskBlock, TaskVector, Topic
from .test_embeddings import fake_embed


def png():
    out = BytesIO()
    Image.new('RGB', (40, 20), 'white').save(out, 'PNG')
    return out.getvalue()


class SearchByMeaningTests(TestCase):
    """Words or a photo's text find the closest tasks by meaning; short queries fall back to word search."""

    def setUp(self):
        # The stand-in model must be in place for setUp too, whatever model this computer has.
        for patcher in (patch.object(embeddings, 'available', return_value=True), patch.object(embeddings, 'embed', side_effect=fake_embed)):
            patcher.start()
            self.addCleanup(patcher.stop)
        cache.clear()
        semantic_search._index.update(stamp=None, ids=[], matrix=None)
        self.user = get_user_model().objects.create_user('teacher', is_staff=True)
        self.client.force_login(self.user)
        topic = Topic.objects.create(number=23, title='Исполнители')
        make = lambda title, text: Task.objects.create(topic=topic, title=title, statement=text, answer='1', published=True, in_catalog=True)
        self.executor = make('Исполнитель', 'Исполнитель превращает число 3 в число 20 командами. Сколько программ?')
        self.primes = make('Простые', 'Определите количество простых чисел в диапазоне от 2 до 20000.')
        self.mask = make('Маска', 'Найдите маску подсети по адресу узла и адресу сети.')
        call_command('embed_tasks', stdout=StringIO())

    def ids_on(self, page):
        return [t.pk for t in page.context['page'].object_list]

    def test_catalogue_orders_by_meaning(self, *_):
        page = self.client.get('/catalog/', {'q': 'сколько простых чисел в отрезке', 'mode': 'meaning'})
        self.assertEqual(self.ids_on(page)[0], self.primes.pk)
        self.assertContains(page, 'По смыслу')
        # One or two words have no meaning to compare: an ordinary word search runs instead.
        self.assertEqual(self.ids_on(self.client.get('/catalog/', {'q': 'маску', 'mode': 'meaning'})), [self.mask.pk])

    def test_kr_picker_by_meaning(self, *_):
        reply = self.client.get('/teacher/kr/tasks/', {'subject': 'inf', 'q': 'исполнитель меняет число командами', 'mode': 'meaning'})
        self.assertEqual(reply.context['cards'][0].pk, self.executor.pk)

    def test_photo_is_read_and_limited(self, *_):
        with patch.object(ocr, 'available', return_value=True), patch.object(ocr, 'read', return_value='Сколько простых чисел в диапазоне'):
            reply = self.client.post('/search/photo/', {'photo': SimpleUploadedFile('task.png', png(), 'image/png')})
            self.assertEqual(reply.json(), {'text': 'Сколько простых чисел в диапазоне'})
            self.assertEqual(self.client.post('/search/photo/').status_code, 400)
            with patch.object(semantic_search, 'allowed_to_read_photo', return_value=False):
                self.assertEqual(self.client.post('/search/photo/', {'photo': SimpleUploadedFile('t.png', png())}).status_code, 429)
        with patch.object(ocr, 'available', return_value=True), patch.object(ocr, 'read', side_effect=ocr.Unreadable('Это не похоже на картинку')):
            self.assertIn('не похоже', self.client.post('/search/photo/', {'photo': SimpleUploadedFile('t.png', b'x')}).json()['error'])
        with patch.object(ocr, 'available', return_value=False):
            self.assertEqual(self.client.post('/search/photo/', {'photo': SimpleUploadedFile('t.png', png())}).status_code, 503)

    def test_pictures_only_conditions_are_read(self, *_):
        import tempfile
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            task = Task.objects.create(topic=Topic.objects.get(number=23), title='Картинка', answer='1', published=True, in_catalog=True)
            name = default_storage.save('imports/kompege/x/23.png', ContentFile(png()))
            TaskBlock.objects.create(task=task, kind='image', purpose='statement', position=1, image=name)
            with patch.object(ocr, 'available', return_value=True), patch.object(ocr, 'read', return_value='Исполнитель с двумя командами'):
                out = StringIO()
                call_command('embed_tasks', '--ocr', stdout=out)
        self.assertIn('conditions read from pictures: 1', out.getvalue())
        self.assertEqual(TaskVector.objects.get(task=task).source_text, 'Исполнитель с двумя командами')
        self.assertIsNotNone(TaskVector.objects.get(task=task).vector)
