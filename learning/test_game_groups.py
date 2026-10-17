import hashlib
import io
import json
from pathlib import Path
import tempfile
import zipfile
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from PIL import Image
from learning.models import Practice, Task, TaskBlock, Topic
from learning.services import create_variant


def jpeg(height, colour):
    data = io.BytesIO()
    Image.new('RGB', (1920, height), colour).save(data, 'JPEG')
    return data.getvalue()


class GameGroupTests(TestCase):
    """KOMPEGE 19–21: one shared condition, three linked tasks, one catalogue card."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        override = self.settings(MEDIA_ROOT=self.root / 'media')
        override.enable()
        self.addCleanup(override.disable)
        self.assets, rows = {}, []
        for ident in (501, 502):
            cond = f'screenshots/19-{ident}-condition.jpg'
            self.assets[cond] = jpeg(200 + ident % 10, 'white')
            for number, answer in ((19, '7'), (20, '28 48'), (21, '44')):
                images = [cond]
                if number != 19:
                    images.append(f'screenshots/{number}-{ident}-question.jpg')
                    self.assets[images[1]] = jpeg(100 + number, 'white')
                rows.append({'number': number, 'source_id': ident, 'source_url': f'https://kompege.ru/task?id={ident}',
                             'answer': answer, 'author': '', 'difficulty': 'easy', 'files': [], 'images': images})
        for row in rows:
            row['images'] = [{'path': p, 'sha256': hashlib.sha256(self.assets[p]).hexdigest()} for p in row['images']]
        self.manifest = {'format': 'infocode-kompege-games-v1', 'source': 'https://kompege.ru', 'numbers': [19, 20, 21],
                         'source_counts': {'19': 2, '20': 2, '21': 2}, 'source_sha256': {n: 'c' * 64 for n in ('19', '20', '21')}, 'tasks': rows}
        self.bundle = self.root / 'games.zip'
        self.write_bundle()
        self.user = get_user_model().objects.create_user('pupil', password='long-test-pass-42')

    def write_bundle(self):
        with zipfile.ZipFile(self.bundle, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(self.manifest))
            for path, data in self.assets.items():
                archive.writestr(path, data)

    def run_import(self, **kwargs):
        out = io.StringIO()
        call_command('import_kompege_numbers', str(self.bundle), numbers=[19, 20, 21], stdout=out, publish=True, **kwargs)
        return out.getvalue()

    def old_pilot_task(self, number=20, ident=501):
        topic, _ = Topic.objects.get_or_create(number=number, defaults={'title': f'№{number}'})
        return Task.objects.create(topic=topic, title='old', answer='1', source_system='kompege', external_id=ident)

    def test_groups_share_one_stored_condition(self):
        self.assertIn('created 6', self.run_import())
        task20 = Task.objects.get(topic__number=20, external_id=501)
        blocks = list(task20.content_blocks.order_by('position'))
        self.assertEqual([b.text for b in blocks], ['Условие КОМПЕГЭ №501', 'Вопрос задания 20'])
        condition19 = Task.objects.get(topic__number=19, external_id=501).content_blocks.get()
        self.assertEqual(blocks[0].image.name, condition19.image.name)
        self.assertEqual(task20.answer, '28 48')

    def test_group_must_share_condition(self):
        self.manifest['tasks'][1]['images'][0] = self.manifest['tasks'][4]['images'][0]
        self.write_bundle()
        with self.assertRaisesMessage(CommandError, 'must share one condition'):
            self.run_import()

    def test_replace_removes_earlier_cards_and_their_files(self):
        old = self.old_pilot_task()
        TaskBlock.objects.create(task=old, kind='text', text='old')
        self.assertIn('Replacing 1 earlier', self.run_import(replace_existing=True))
        self.assertFalse(Task.all_objects.filter(title='old').exists())
        self.assertEqual(Task.objects.filter(topic__number=20).count(), 2)

    def test_replace_refuses_when_pupils_answered(self):
        Practice.objects.create(user=self.user, task=self.old_pilot_task(), answer='1', correct=True)
        with self.assertRaisesMessage(CommandError, 'Refusing to replace'):
            self.run_import(replace_existing=True)
        self.assertTrue(Task.all_objects.filter(title='old').exists())

    def test_catalogue_shows_one_card_with_three_answers(self):
        self.run_import()
        self.client.force_login(self.user)
        page = self.client.get('/catalog/?number=20')
        self.assertContains(page, 'Задания № 19–21')
        self.assertContains(page, 'Найдено: 2')
        self.assertEqual(page.content.decode().count('data-catalog-answer'), 6)
        self.assertContains(page, 'Задание 21')
        everything = self.client.get('/catalog/').content.decode()
        self.assertEqual(everything.count('data-catalog-answer'), 6)
        self.assertIn('>19–21</a>', everything)
        self.assertNotIn('href="?number=20"', everything)

    def test_follow_up_mistake_brings_its_group(self):
        self.run_import()
        task21 = Task.objects.get(topic__number=21, external_id=502)
        Practice.objects.create(user=self.user, task=task21, answer='1', correct=False)
        self.client.force_login(self.user)
        page = self.client.get('/catalog/?state=mistakes')
        self.assertContains(page, 'Найдено: 1')
        self.assertContains(page, f'id="task-{task21.pk}"')

    def test_answering_follow_up_records_its_own_task(self):
        self.run_import()
        task20 = Task.objects.get(topic__number=20, external_id=501)
        self.client.force_login(self.user)
        page = self.client.get('/catalog/?number=19')
        token = page.context['page'].object_list[0].follow_ups[0].submit_token
        reply = self.client.post('/catalog/?number=19', {'task_id': task20.pk, 'submit_token': token, f'task-{task20.pk}-answer': '28 48'},
                                 HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertTrue(reply.json()['solved'])
        self.assertTrue(Practice.objects.get(user=self.user).task_id == task20.pk)

    def test_variant_takes_20_and_21_from_the_chosen_19_group(self):
        self.run_import()
        topics = Topic.objects.filter(number__in=[19, 20, 21])
        for _ in range(5):
            variant = create_variant(creator=self.user, title='v', topics=topics)
            items = {i.number: i.task.external_id for i in variant.items.select_related('task')}
            self.assertEqual(len(set(items.values())), 1, items)
