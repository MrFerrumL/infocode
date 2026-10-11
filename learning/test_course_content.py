import importlib
import io
import tempfile
from types import SimpleNamespace
from PIL import Image
from django.apps import apps
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, SimpleTestCase, override_settings
from django.db import connection
from django.utils import timezone
from .models import Course, Section, Lesson, Step, StepContentBlock, Enrollment, StepProgress, StepSubmission, MediaAsset, VideoPosition, StudyGroup
from .rich_text import sanitize_html, safe_link


class RichTextTests(SimpleTestCase):
    def test_rich_formatting_and_code_are_preserved(self):
        value = '<h2>Тема</h2><p><b>Жирный</b> <u>линия</u> <i>курсив</i></p><ol><li>x &lt; 2</li></ol>'
        clean = sanitize_html(value)
        self.assertIn('<strong>Жирный</strong>', clean)
        self.assertIn('<u>линия</u>', clean)
        self.assertIn('x &lt; 2', clean)
        self.assertEqual(sanitize_html(clean), clean)

    def test_active_html_and_dangerous_links_are_removed(self):
        value = '<img src=x onerror=alert(1)><svg><script>alert(1)</script></svg><p style="background:url(x)" onclick="bad()"><a href="java&#10;script:alert(1)">bad</a><iframe srcdoc="bad"></iframe><strong>OK</strong></p>'
        clean = sanitize_html(value)
        for bad in ('<script', '<img', '<svg', '<iframe', 'onclick', 'onerror', 'style=', 'href='):
            self.assertNotIn(bad, clean)
        self.assertIn('<strong>OK</strong>', clean)
        for url in ['//evil.test', 'javascript:alert(1)', 'data:text/html,x', 'https://user:password@test', 'https:\\evil.test']:
            self.assertEqual(safe_link(url), '')
        self.assertEqual(safe_link('https://example.com/a?x=1'), 'https://example.com/a?x=1')


class CourseContentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user('content-author', is_staff=True)
        cls.other = User.objects.create_user('content-other', is_staff=True)
        cls.student = User.objects.create_user('content-student')
        cls.course = Course.objects.create(title='Конструктор', owner=cls.teacher, published=True)
        cls.section = Section.objects.create(course=cls.course, title='Раздел')
        cls.lesson = Lesson.objects.create(section=cls.section, title='Урок', published=True)
        cls.step = Step.objects.create(lesson=cls.lesson, title='Теория', body='Старый текст', points=0)
        Enrollment.objects.create(user=cls.student, course=cls.course)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.folder.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.client.force_login(self.teacher)

    def image(self):
        buf = io.BytesIO()
        Image.new('RGB', (16, 16), 'purple').save(buf, format='PNG')
        return SimpleUploadedFile('photo.png', buf.getvalue(), content_type='image/png')

    def url(self, step=None):
        step = step or self.step
        return f'/teacher/lessons/{step.lesson_id}/steps/{step.pk}/'

    def payload(self, rows, step=None, **extra):
        step = step or self.step
        data = {'title': step.title, 'kind': step.kind, 'published': 'on', 'points': 0, 'max_attempts': 0,
                'edit_version': step.edit_version, 'blocks-TOTAL_FORMS': len(rows),
                'blocks-INITIAL_FORMS': step.content_blocks.count()}
        for i, row in enumerate(rows):
            data[f'blocks-{i}-position'] = i+1
            for key, value in row.items():
                data[f'blocks-{i}-{key}'] = value
        data.update(extra)
        return data

    def save_rows(self, rows, step=None, **extra):
        response = self.client.post(self.url(step), self.payload(rows, step, **extra))
        if response.status_code != 302:
            self.fail(str(response.context['form'].errors) + str(response.context['blocks'].errors) + str(response.context['blocks'].non_form_errors()))
        (step or self.step).refresh_from_db()
        return response

    def test_editor_renders_legacy_material_without_writing_on_get(self):
        response = self.client.get(self.url())
        self.assertContains(response, 'data-content-blocks')
        self.assertContains(response, 'Старый текст')
        self.assertFalse(self.step.content_blocks.exists())

    def test_save_interleaved_content_and_render_for_student(self):
        rows = [{'kind': 'text', 'text': '<p><strong>Начало</strong> <u>важно</u></p>'},
                {'kind': 'image', 'image': self.image(), 'caption': 'Схема'},
                {'kind': 'text', 'text': '<h2>После картинки</h2>'},
                {'kind': 'video', 'video': SimpleUploadedFile('v.mp4', b'0123456789')},
                {'kind': 'code', 'text': 'if x < 3:\n    print("<script>safe</script>")'},
                {'kind': 'file', 'file': SimpleUploadedFile('data.txt', b'42')}, {'kind': 'divider'}]
        self.save_rows(rows)
        self.assertTrue(self.step.use_blocks)
        self.assertEqual(list(self.step.content_blocks.values_list('kind', flat=True)), [r['kind'] for r in rows])
        self.client.force_login(self.student)
        page = self.client.get(f'/learn/{self.step.pk}/')
        self.assertContains(page, '<strong>Начало</strong>')
        self.assertContains(page, '<u>важно</u>')
        self.assertNotContains(page, 'Старый текст')
        self.assertNotContains(page, '<script>safe</script>')
        self.assertContains(page, '&lt;script&gt;safe&lt;/script&gt;')
        self.assertLess(page.content.index('Схема'.encode()), page.content.index('После картинки'.encode()))

    def test_noop_conversion_keeps_progress_but_content_change_invalidates_it(self):
        StepProgress.objects.create(user=self.student, step=self.step, revision=1, completed=True)
        self.save_rows([{'kind': 'text', 'text': '<p>Старый текст</p>'}])
        self.assertEqual(self.step.revision, 1)
        block = self.step.content_blocks.get()
        self.save_rows([{'id': block.pk, 'kind': 'text', 'text': '<p>Новый текст</p>'}])
        self.assertEqual(self.step.revision, 2)
        self.assertEqual(StepProgress.objects.get(step=self.step).revision, 1)
        self.assertEqual(StepProgress.objects.count(), 1)

    def test_reorder_duplicate_delete_and_noop_save(self):
        self.save_rows([{'kind': 'text', 'text': '<p>A</p>'}, {'kind': 'text', 'text': '<p>B</p>'}])
        a, b = self.step.content_blocks.all()
        self.save_rows([{'id': a.pk, 'kind': 'text', 'text': a.text, 'position': 2}, {'id': b.pk, 'kind': 'text', 'text': b.text, 'position': 1}, {'kind': 'text', 'text': '<p>C</p>'}])
        self.assertEqual(list(self.step.content_blocks.values_list('text', flat=True)), ['<p>B</p>', '<p>A</p>', '<p>C</p>'])
        b, a, c = self.step.content_blocks.all()
        self.save_rows([{'id': b.pk, 'kind': 'text', 'text': b.text}, {'id': a.pk, 'DELETE': 'on'}, {'id': c.pk, 'kind': 'text', 'text': c.text}])
        self.assertFalse(StepContentBlock.objects.filter(pk=a.pk).exists())
        version = self.step.revision
        self.save_rows([{'id': b.pk, 'kind': 'text', 'text': b.text}, {'id': c.pk, 'kind': 'text', 'text': c.text}])
        self.assertEqual(self.step.revision, version)

    def test_stale_editor_rejected_without_overwriting(self):
        data = self.payload([{'kind': 'text', 'text': '<p>first</p>'}])
        self.assertEqual(self.client.post(self.url(), data).status_code, 302)
        data['title'] = 'overwrite'
        response = self.client.post(self.url(), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'другой вкладке')
        self.step.refresh_from_db()
        self.assertNotEqual(self.step.title, 'overwrite')

    def test_forged_block_id_or_source_cannot_steal_media(self):
        other_step = Step.objects.create(lesson=self.lesson, title='Другой')
        foreign = StepContentBlock.objects.create(step=other_step, kind='image', image=self.image())
        for row in [{'id': foreign.pk, 'kind': 'text', 'text': 'hack'}, {'copy_from': foreign.pk, 'kind': 'image'}]:
            response = self.client.post(self.url(), self.payload([row]))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(self.step.content_blocks.exists())
        foreign.refresh_from_db()
        self.assertEqual(foreign.step_id, other_step.pk)

    def test_missing_existing_block_id_is_not_silently_deleted(self):
        self.save_rows([{'kind': 'text', 'text': 'A'}])
        response = self.client.post(self.url(), self.payload([]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.step.content_blocks.count(), 1)

    def test_invalid_upload_rolls_back_entire_edit(self):
        data = self.payload([{'kind': 'text', 'text': 'Good'}, {'kind': 'image', 'image': SimpleUploadedFile('fake.png', b'<html>not an image</html>')}], title='BAD')
        response = self.client.post(self.url(), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.step.content_blocks.exists())
        self.step.refresh_from_db()
        self.assertEqual(self.step.title, 'Теория')

    def test_media_library_owner_type_and_reuse(self):
        asset = MediaAsset.objects.create(owner=self.teacher, kind='image', title='Фото', file=self.image())
        self.save_rows([{'kind': 'image', 'asset': asset.pk}])
        self.assertEqual(self.step.content_blocks.get().image.name, asset.file.name)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.client.force_login(self.teacher)
        foreign = MediaAsset.objects.create(owner=self.other, kind='image', title='Чужое', file=self.image())
        block = self.step.content_blocks.get()
        response = self.client.post(self.url(), self.payload([{'id': block.pk, 'kind': 'image', 'asset': foreign.pk}]))
        self.assertEqual(response.status_code, 200)

    def test_duplicate_media_without_reupload(self):
        self.save_rows([{'kind': 'image', 'image': self.image()}])
        block = self.step.content_blocks.get()
        self.save_rows([{'id': block.pk, 'kind': 'image'}, {'kind': 'image', 'copy_from': block.pk}])
        self.assertEqual(self.step.content_blocks.count(), 2)
        self.assertEqual(len(set(self.step.content_blocks.values_list('image', flat=True))), 1)

    def test_private_download_group_sequential_and_archive(self):
        self.save_rows([{'kind': 'image', 'image': self.image()}])
        block = self.step.content_blocks.get()
        url = f'/files/course-block/{block.pk}/'
        self.client.force_login(self.student)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response.close()
        group = StudyGroup.objects.create(title='Закрытый', teacher=self.teacher)
        self.course.group = group
        self.course.save()
        self.assertEqual(self.client.get(url).status_code, 404)
        group.students.add(self.student)
        self.course.sequential = True
        self.course.save()
        Step.objects.create(lesson=self.lesson, title='Ранее', body='Текст', position=0)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.course.archived_at = timezone.now()
        self.course.save()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_video_range_and_independent_resume(self):
        self.save_rows([{'kind': 'video', 'video': SimpleUploadedFile('one.mp4', b'0123456789')}, {'kind': 'video', 'video': SimpleUploadedFile('two.mp4', b'abcdefghij')}])
        a, b = self.step.content_blocks.all()
        self.client.force_login(self.student)
        response = self.client.get(f'/files/course-block/{a.pk}/', HTTP_RANGE='bytes=2-5')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(b''.join(response.streaming_content), b'2345')
        response.close()
        for block, seconds in [(a, 12), (b, 30)]:
            url = f'/learn/{self.step.pk}/position/?block={block.pk}'
            self.assertEqual(self.client.post(url, {'seconds': seconds}).status_code, 200)
        self.assertEqual(self.client.get(f'/learn/{self.step.pk}/position/?block={a.pk}').json()['seconds'], 12)
        self.assertEqual(self.client.get(f'/learn/{self.step.pk}/position/?block={b.pk}').json()['seconds'], 30)
        self.step.revision += 1
        self.step.save()
        self.assertEqual(self.client.get(f'/learn/{self.step.pk}/position/?block={a.pk}').json()['seconds'], 0)

    def test_quiz_with_rich_condition_still_checks_answers(self):
        self.step.kind = 'quiz'
        self.step.options, self.step.correct_choices = ['A', 'B'], [1]
        self.step.save()
        self.save_rows([{'kind': 'text', 'text': '<p><u>Выберите</u> ответ</p>'}], points=2, **{'options-TOTAL_FORMS': 2, 'options-INITIAL_FORMS': 2, 'options-0-text': 'A', 'options-1-text': 'B', 'options-1-correct': 'on'})
        self.client.force_login(self.student)
        self.client.post(f'/learn/{self.step.pk}/', {'choice': '1'})
        self.assertTrue(StepSubmission.objects.get(step=self.step).correct)

    def test_course_clone_copies_blocks_but_not_progress(self):
        self.save_rows([{'kind': 'text', 'text': '<p>Текст</p>'}, {'kind': 'image', 'image': self.image()}])
        StepProgress.objects.create(user=self.student, step=self.step, completed=True, revision=self.step.revision)
        self.client.post(f'/teacher/courses/{self.course.pk}/copy/')
        copy = Course.objects.exclude(pk=self.course.pk).get()
        new_step = Step.objects.get(lesson__section__course=copy)
        self.assertEqual(new_step.content_blocks.count(), 2)
        self.assertFalse(new_step.published)
        self.assertFalse(StepProgress.objects.filter(step=new_step).exists())

    def test_migration_keeps_order_files_and_progress(self):
        self.step.code = 'print(42)'
        self.step.image = self.image()
        self.step.save()
        progress = StepProgress.objects.create(user=self.student, step=self.step, completed=True)
        migration = importlib.import_module('learning.migrations.0008_migrate_course_material')
        migration.move_legacy_material(apps, SimpleNamespace(connection=connection))
        self.step.refresh_from_db()
        progress.refresh_from_db()
        self.assertTrue(self.step.use_blocks)
        self.assertEqual(self.step.revision, progress.revision)
        self.assertEqual(list(self.step.content_blocks.values_list('kind', flat=True)), ['text', 'code', 'image'])
        self.assertEqual(self.step.content_blocks.get(kind='image').image.name, self.step.image.name)
        migration.move_legacy_material(apps, SimpleNamespace(connection=connection))
        self.assertEqual(self.step.content_blocks.count(), 3)

    def test_model_and_render_both_sanitize_html(self):
        self.save_rows([{'kind': 'text', 'text': '<p onclick="bad()"><b>OK</b><img src=x onerror=bad()></p>'}])
        block = self.step.content_blocks.get()
        self.assertEqual(block.text, '<p><strong>OK</strong></p>')
        StepContentBlock.objects.filter(pk=block.pk).update(text='<svg onload="bad()"></svg><u>safe</u>')
        self.client.force_login(self.student)
        response = self.client.get(f'/learn/{self.step.pk}/')
        self.assertNotContains(response, '<svg')
        self.assertContains(response, '<u>safe</u>')

    def test_empty_material_cannot_publish_but_can_remain_draft(self):
        response = self.client.post(self.url(), self.payload([]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'хотя бы один блок')
        self.save_rows([], published='')
        self.assertFalse(self.step.published)
        self.assertTrue(self.step.use_blocks)

    def test_legacy_file_route_is_closed_after_conversion(self):
        self.step.image = self.image()
        self.step.save()
        self.save_rows([{'kind': 'text', 'text': 'Новое условие без фото'}])
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(f'/files/step-image/{self.step.pk}/').status_code, 404)
