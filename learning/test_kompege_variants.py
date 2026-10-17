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
from learning.models import ExamAnswer, ExamAttempt, Task, Topic, Variant
from learning.services import EGE_SCALE, ege_points
from learning.security import form_token


def jpeg():
    data = io.BytesIO()
    Image.new('RGB', (1920, 300), 'white').save(data, 'JPEG')
    return data.getvalue()


class KompegeVariantTests(TestCase):
    """Variants from the KOMPEGE archive: hidden tasks, exam station and exam scoring."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        override = self.settings(MEDIA_ROOT=self.root / 'media')
        override.enable()
        self.addCleanup(override.disable)
        self.admin = get_user_model().objects.create_superuser('owner', password='x')
        self.pupil = get_user_model().objects.create_user('pupil', password='x')
        self.catalog = Task.objects.create(topic=Topic.objects.create(number=1, title='Графы'), title='cat', statement='x',
                                           answer='35', source_system='kompege', external_id=100)
        image = jpeg()
        self.assets = {'screenshots/26-200-condition.jpg': image}
        hidden = {'number': 26, 'source_id': 200, 'source_type': 26, 'source_url': 'https://kompege.ru/task?id=200',
                  'author': 'Автор', 'difficulty': 'hard', 'answer': '9517 46436', 'answer_origin': 'variant', 'files': [],
                  'images': [{'path': 'screenshots/26-200-condition.jpg', 'sha256': hashlib.sha256(image).hexdigest()}]}
        variant = {'kim': 25203213, 'title': 'Основная волна 18.06.26', 'school_year': '2025/26', 'exam_year': 2026,
                   'collection': 'Открытые пробники и реальные варианты', 'description': '', 'hide_answers': False, 'order': 1,
                   'source_url': 'https://kompege.ru/variant?kim=25203213',
                   'items': [{'position': 1, 'number': 1, 'topic': 1, 'source_type': 1, 'source_id': 100, 'answer': '35', 'rows': 1, 'cols': 1},
                             {'position': 2, 'number': 26, 'topic': 26, 'source_type': 26, 'source_id': 200, 'answer': '9517 46436', 'rows': 1, 'cols': 2}]}
        self.manifest = {'format': 'infocode-kompege-variants-v1', 'source': 'https://kompege.ru', 'numbers': [26],
                         'source_counts': {'26': 1}, 'source_sha256': {'26': 'a' * 64}, 'tasks': [hidden], 'variants': [variant]}
        self.bundle = self.root / 'variants.zip'
        self.write()

    def write(self):
        with zipfile.ZipFile(self.bundle, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(self.manifest))
            for path, data in self.assets.items():
                archive.writestr(path, data)

    def run_import(self, **kwargs):
        out = io.StringIO()
        call_command('import_kompege_variants', str(self.bundle), stdout=out, publish=True, **kwargs)
        return out.getvalue()

    def test_import_creates_labelled_variant_and_hidden_task(self):
        self.assertIn('1 new variants, 1 hidden tasks', self.run_import())
        variant = Variant.objects.get(external_id=25203213)
        self.assertEqual(variant.title, 'ЕГЭ 2026 · Основная волна 18.06.26')
        self.assertEqual((variant.scoring, variant.school_year, variant.duration), ('ege', '2025/26', 235))
        hidden = Task.objects.get(external_id=200)
        self.assertFalse(hidden.in_catalog)
        self.assertEqual([i.task_id for i in variant.items.all()], [self.catalog.pk, hidden.pk])
        self.assertEqual(variant.items.get(number=26).answer_cols, 2)
        self.assertIn('0 new variants, 0 hidden tasks', self.run_import())

    def test_missing_catalogue_task_is_refused(self):
        self.catalog.delete()
        with self.assertRaisesMessage(CommandError, 'not on this site'):
            self.run_import()
        self.assertFalse(Variant.objects.exists())

    def test_hidden_task_stays_out_of_the_catalogue(self):
        self.run_import()
        self.client.force_login(self.pupil)
        hidden = Task.objects.get(external_id=200)
        self.assertNotContains(self.client.get('/catalog/?number=26'), f'id="task-{hidden.pk}"')
        self.assertEqual(self.client.get(f'/tasks/{hidden.pk}/').status_code, 404)

    def test_archive_lists_variant_under_year_and_collection(self):
        self.run_import()
        self.client.force_login(self.pupil)
        page = self.client.get('/variants/')
        self.assertContains(page, 'ЕГЭ 2026')
        self.assertContains(page, '2025/26 учебный год')
        self.assertContains(page, 'Открытые пробники и реальные варианты')
        self.assertContains(page, '>Основная волна 18.06.26<')

    def test_station_saves_grid_answers_and_scores_like_the_exam(self):
        self.run_import()
        variant = Variant.objects.get()
        self.client.force_login(self.pupil)
        self.client.post(f'/variants/{variant.pk}/start/', {'submit_token': form_token(self.pupil, f'start-exam:{variant.pk}')})
        attempt = ExamAttempt.objects.get()
        page = self.client.get(f'/exam/{attempt.pk}/')
        self.assertTemplateUsed(page, 'exam_station.html')
        item1, item26 = variant.items.order_by('position')
        self.assertContains(page, f'name="cell_{item26.pk}_0_1"')
        saved = self.client.post(f'/exam/{attempt.pk}/save/', {'item': item26.pk, f'cell_{item26.pk}_0_0': '46436', f'cell_{item26.pk}_0_1': '9517'})
        self.assertEqual(saved.json(), {'ok': True, 'value': '46436 9517', 'answered': 1})
        self.client.post(f'/exam/{attempt.pk}/', {'submit_token': form_token(self.pupil, f'exam:{attempt.pk}'), 'action': 'submit',
                                                   f'cell_{item1.pk}_0_0': '35', f'cell_{item26.pk}_0_0': '46436', f'cell_{item26.pk}_0_1': '9517'})
        attempt.refresh_from_db()
        self.assertEqual((attempt.primary_score, attempt.test_score), (2, EGE_SCALE[2]))
        self.assertEqual(ExamAnswer.objects.get(item=item26).points, 1)
        result = self.client.get(f'/results/{attempt.pk}/')
        self.assertContains(result, 'Первичный балл')
        self.assertContains(result, '9517 46436')

    def test_hidden_answers_are_not_shown_to_pupils(self):
        self.manifest['variants'][0]['hide_answers'] = True
        self.write()
        self.run_import()
        variant = Variant.objects.get()
        attempt = ExamAttempt.objects.create(user=self.pupil, variant=variant, deadline='2099-01-01T00:00Z')
        self.client.force_login(self.pupil)
        self.client.post(f'/exam/{attempt.pk}/', {'submit_token': form_token(self.pupil, f'exam:{attempt.pk}'), 'action': 'submit'})
        page = self.client.get(f'/results/{attempt.pk}/')
        self.assertContains(page, 'Ответ скрыт')
        self.assertNotContains(page, '9517 46436')

    def test_kege_partial_credit_rules(self):
        self.assertEqual(ege_points(27, '3 1\n4601 13', '3 129201\n4601 13'), 1)
        self.assertEqual(ege_points(26, '1 46436', '9517 46436'), 1)
        self.assertEqual(ege_points(12, 'ABC', 'abc'), 1)
        self.assertEqual(ege_points(12, '', 'abc'), 0)
