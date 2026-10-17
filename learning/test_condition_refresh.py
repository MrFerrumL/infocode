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


class ConditionImageRefreshTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        override=self.settings(MEDIA_ROOT=self.root/'media');override.enable();self.addCleanup(override.disable)
        self.user=get_user_model().objects.create_user('image-owner')
        self.task=Task.objects.create(topic=Topic.objects.create(number=1,title='Графы'),title='Исходная задача',answer='42',source_system='kompege',external_id=100)
        self.old='imports/kompege/100/abcdef123456/condition.jpg'
        old_path=self.root/'media'/self.old;old_path.parent.mkdir(parents=True);old_path.write_bytes(b'original-condition')
        self.block=TaskBlock.objects.create(task=self.task,kind='image',image=self.old)
        image=io.BytesIO();Image.new('RGB',(1920,120),'white').save(image,'PNG');self.data=image.getvalue()
        self.asset={'path':'images/condition.png','previous_name':self.old,'previous_sha256':hashlib.sha256(b'original-condition').hexdigest(),'sha256':hashlib.sha256(self.data).hexdigest()}
        self.bundle=self.root/'images.zip';self.write_bundle()

    def write_bundle(self):
        with zipfile.ZipFile(self.bundle,'w') as archive:
            archive.writestr('manifest.json',json.dumps({'format':'infocode-condition-images-v1','assets':[self.asset]}))
            archive.writestr(self.asset['path'],self.data)

    def run_refresh(self,**kwargs):
        output=io.StringIO();call_command('refresh_kompege_images',str(self.bundle),stdout=output,**kwargs);return output.getvalue()

    def test_refresh_keeps_task_attempts_and_original_snapshot_then_is_idempotent(self):
        Practice.objects.create(user=self.user,task=self.task,answer='42',correct=True)
        variant=create_variant(creator=self.user,title='Сохранённая работа',topics=[],count=1,task_ids=str(self.task.pk))
        self.assertIn('updated 1 blocks',self.run_refresh())
        self.block.refresh_from_db();self.assertIn('/hq-',str(self.block.image))
        self.assertEqual((self.root/'media'/str(self.block.image)).read_bytes(),self.data)
        self.assertEqual((self.root/'media'/self.old).read_bytes(),b'original-condition')
        self.assertEqual(variant.items.get().blocks[0]['image'],self.old)
        self.assertEqual(Practice.objects.count(),1);self.task.refresh_from_db();self.assertEqual(self.task.answer,'42')
        self.assertIn('updated 0 blocks',self.run_refresh())

    def test_dry_run_writes_nothing_and_edited_image_is_not_overwritten(self):
        self.run_refresh(dry_run=True);self.block.refresh_from_db();self.assertEqual(str(self.block.image),self.old)
        (self.root/'media'/self.old).write_bytes(b'teacher-edited-original')
        with self.assertRaisesMessage(CommandError,'refusing to overwrite'): self.run_refresh()
        self.block.refresh_from_db();self.assertEqual(str(self.block.image),self.old)
        self.assertEqual(len(list((self.root/'media').rglob('*.png'))),0)

    def test_image_replaced_by_teacher_is_skipped(self):
        self.block.image='task-images/teacher.png';self.block.save()
        self.assertIn('updated 0 blocks',self.run_refresh())
        self.block.refresh_from_db();self.assertEqual(str(self.block.image),'task-images/teacher.png')

    def test_bad_checksum_and_traversal_are_rejected_without_changing_block(self):
        self.asset['sha256']='0'*64;self.write_bundle()
        with self.assertRaisesMessage(CommandError,'checksum mismatch'): self.run_refresh()
        self.asset['sha256']=hashlib.sha256(self.data).hexdigest();self.asset['path']='../outside.png';self.write_bundle()
        with self.assertRaisesMessage(CommandError,'Unsafe image member path'): self.run_refresh()
        self.block.refresh_from_db();self.assertEqual(str(self.block.image),self.old)
