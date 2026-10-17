from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import zipfile
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from deploy.condition_render import Condition
from deploy.prepare_kompege_numbers import source_answer, ANSWER_RECOVERIES
from learning.models import Task, TaskBlock, Topic, Practice


class CompleteCategoriesTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        override = self.settings(MEDIA_ROOT=self.root / 'media')
        override.enable()
        self.addCleanup(override.disable)
        image = io.BytesIO()
        Image.new('RGB', (1920, 200), 'white').save(image, 'JPEG')
        self.assets = {'screenshots/condition.jpg': image.getvalue(), 'attachments/data.ods': b'original ODS fixture'}
        self.manifest = {'format':'infocode-kompege-numbers-v1','source':'https://kompege.ru',
            'numbers':[2,3],'source_counts':{'2':2,'3':1},'source_sha256':{'2':'a'*64,'3':'b'*64},'tasks':[]}
        for number, ident in ((2,31499),(2,25),(3,31500)):
            self.manifest['tasks'].append({'number':number,'source_id':ident,'source_url':f'https://kompege.ru/task?id={ident}',
                'answer':'0012','author':'Автор','difficulty':'easy','images':[self.asset('screenshots/condition.jpg')],
                'files':[dict(self.asset('attachments/data.ods'),name='3.ods')] if number == 3 else []})
        self.bundle = self.root / 'bank.zip'
        self.write_bundle()

    def asset(self, path):
        return {'path':path,'sha256':hashlib.sha256(self.assets[path]).hexdigest()}

    def write_bundle(self):
        with zipfile.ZipFile(self.bundle,'w') as archive:
            archive.writestr('manifest.json',json.dumps(self.manifest))
            for path,data in self.assets.items():
                archive.writestr(path,data)

    def run_import(self, **kwargs):
        out=io.StringIO()
        call_command('import_kompege_numbers',str(self.bundle),numbers=[2,3],stdout=out,**kwargs)
        return out.getvalue()

    def test_complete_import_preserves_original_file_download_and_answer(self):
        self.assertIn('created 3',self.run_import(publish=True))
        self.assertEqual(Counter(Task.objects.values_list('topic__number',flat=True)),{2:2,3:1})
        self.assertEqual(Task.objects.filter(answer='0012',published=True).count(),3)
        file=TaskBlock.objects.get(kind='file')
        student=get_user_model().objects.create_user('files-reader')
        self.client.force_login(student)
        response=self.client.get(reverse('download',args=['block',file.pk]))
        self.assertEqual(response.status_code,200)
        self.assertEqual(b''.join(response.streaming_content),self.assets['attachments/data.ods'])
        self.assertIn('3.ods',response['Content-Disposition'])

    def test_repeat_keeps_edits_archives_history_and_other_categories(self):
        self.run_import(publish=True)
        task=Task.objects.get(external_id=31499)
        task.answer='9';task.title='Правка';task.archived_at=timezone.now();task.save()
        first=Task.objects.create(topic=Topic.objects.create(number=1,title='Моя тема'),title='Первый номер',answer='7')
        user=get_user_model().objects.create_user('old-pupil')
        Practice.objects.create(user=user,task=task,answer='9',correct=True)
        self.assertIn('created 0, skipped 3',self.run_import(publish=True))
        task.refresh_from_db();first.refresh_from_db()
        self.assertEqual((task.answer,task.title,first.answer),('9','Правка','7'))
        self.assertIsNotNone(task.archived_at)
        self.assertEqual(Practice.objects.get().task_id,task.pk)

    def test_dry_run_is_read_only_and_scope_cannot_expand(self):
        self.assertIn('Dry run',self.run_import(dry_run=True))
        self.assertFalse(Topic.objects.exists())
        with self.assertRaisesMessage(CommandError,'do not match'):
            call_command('import_kompege_numbers',str(self.bundle),numbers=[2])
        self.assertFalse((self.root/'media').exists())

    def test_missing_category_rows_duplicates_and_checksums_fail_before_writes(self):
        self.manifest['source_counts']['3']=2
        self.write_bundle()
        with self.assertRaisesMessage(CommandError,'incomplete'):self.run_import()
        self.manifest['source_counts']['3']=1
        self.manifest['tasks'][1]['source_id']=31499
        self.write_bundle()
        with self.assertRaisesMessage(CommandError,'unique'):self.run_import()
        self.manifest['tasks'][1]['source_id']=25
        self.assets['attachments/data.ods']=b'altered'
        self.write_bundle()
        with self.assertRaisesMessage(CommandError,'checksum'):self.run_import()
        self.assertFalse(Topic.objects.exists())

    def test_failed_block_validation_rolls_back_new_media_and_all_rows(self):
        with patch.object(TaskBlock,'full_clean',side_effect=ValidationError('failed block')):
            with self.assertRaises(ValidationError):self.run_import()
        self.assertFalse(Task.all_objects.exists())
        self.assertFalse(any(p.is_file() for p in (self.root/'media').rglob('*')))

    def test_native_renderer_keeps_math_table_and_restricts_embedded_origins(self):
        resolved=[]
        def resolver(url):
            resolved.append(url)
            return 'source-images/original.gif'
        parser=Condition(image_resolver=resolver)
        parser.feed('<p>\\(x \\lor y\\)</p><table border="1"><tr><td>0</td></tr></table><img><img src="https://kpolyakov.spb.ru/cms/images/5271.gif">')
        self.assertIn('\\(x \\lor y\\)',''.join(parser.parts))
        self.assertIn('<table border="1">',''.join(parser.parts))
        self.assertEqual(len(resolved),1)
        with self.assertRaises(ValueError):Condition().feed('<img src="https://other.example/image.png">')
        with self.assertRaises(ValueError):Condition().feed('<script>evil()</script>')

    def test_native_renderer_preserves_code_indentation_and_lists_as_in_source(self):
        parser=Condition()
        parser.feed('<pre><code>for x in range(3):\n    print(x &lt; 2)</code></pre><ol><li>Шаг</li></ol>')
        html=''.join(parser.parts)
        self.assertIn('for x in range(3):\n    print(x &lt; 2)',html)
        self.assertIn('<ol><li>Шаг</li></ol>',html)

    def test_missing_key_recovery_is_bound_to_verified_condition_and_solution(self):
        row={'number':7,'taskId':31372,'key':'','text':'condition','solve_text':'original solution'}
        recovery={'answer':'120','condition_sha256':hashlib.sha256(row['text'].encode()).hexdigest(),
                  'solution_sha256':hashlib.sha256(row['solve_text'].encode()).hexdigest()}
        with patch.dict(ANSWER_RECOVERIES,{(7,31372):recovery},clear=True):
            self.assertEqual(source_answer(row),('120','author-solution'))
            row['text']='changed condition'
            with self.assertRaises(ValueError):source_answer(row)
            row['key']='0012'
            self.assertEqual(source_answer(row),('0012','api'))
        with self.assertRaises(ValueError):source_answer({'number':8,'taskId':123,'key':''})
