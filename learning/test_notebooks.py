import io
import json
import tempfile
import zipfile
from pathlib import Path
from PIL import Image
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from .models import (Notebook, NotebookCell, StudyGroup, Course, Section, Lesson, Step,
                     StepContentBlock, Enrollment, StepProgress, StepSubmission, LessonProgress,
                     TrashEntry, EditorDraft, MediaAsset)
from .course_services import current_progress
from .platform_views import archive
from .studio_views import clone_step


class NotebookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user('notebook-author', is_staff=True)
        cls.other = User.objects.create_user('notebook-other', is_staff=True)
        cls.admin = User.objects.create_user('notebook-admin', is_staff=True, is_superuser=True)
        cls.student = User.objects.create_user('notebook-student')
        cls.outsider = User.objects.create_user('notebook-outsider')
        cls.group = StudyGroup.objects.create(title='Группа автора', teacher=cls.teacher)
        cls.group.students.add(cls.student)
        cls.book = Notebook.objects.create(owner=cls.teacher, title='Системы счисления', published=True)
        cls.cell = NotebookCell.objects.create(notebook=cls.book, kind='text', text='<h2>Основание системы</h2>')
        cls.course = Course.objects.create(owner=cls.teacher, title='Курс', published=True)
        cls.section = Section.objects.create(course=cls.course, title='Раздел')
        cls.lesson = Lesson.objects.create(section=cls.section, title='Урок', published=True)
        cls.step = Step.objects.create(lesson=cls.lesson, kind='notebook', title='Конспект урока', published=True, use_blocks=True, points=0)
        cls.link = StepContentBlock.objects.create(step=cls.step, kind='notebook', notebook=cls.book)
        Enrollment.objects.create(user=cls.student, course=cls.course)

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.media_override = override_settings(MEDIA_ROOT=folder.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.client.force_login(self.teacher)

    def image(self):
        stream = io.BytesIO()
        Image.new('RGB', (16, 16), 'orange').save(stream, format='PNG')
        return SimpleUploadedFile('figure.png', stream.getvalue(), content_type='image/png')

    def url(self, book=None):
        return f'/teacher/notebooks/{(book or self.book).pk}/edit/'

    def payload(self, rows, book=None, **extra):
        book = book or self.book
        data = {'title': book.title, 'description': book.description, 'group': book.group_id or '',
                'published': 'on', 'edit_version': book.edit_version, 'blocks-TOTAL_FORMS': len(rows),
                'blocks-INITIAL_FORMS': book.cells.count() if book.pk else 0}
        for i, row in enumerate(rows):
            data[f'blocks-{i}-position'] = i+1
            for key, value in row.items():
                data[f'blocks-{i}-{key}'] = value
        data.update(extra)
        return data

    def save(self, rows, **extra):
        response = self.client.post(self.url(), self.payload(rows, **extra))
        if response.status_code != 302:
            self.fail(str(response.context['form'].errors) + str(response.context['blocks'].errors) + str(response.context['blocks'].non_form_errors()))
        self.book.refresh_from_db()
        return response

    def existing(self, **change):
        return dict(id=self.cell.pk, kind='text', text='<h2>Основание системы</h2>', **change)

    def test_new_editor_does_not_save_on_get_and_has_no_unsaved_relation_error(self):
        before = Notebook.objects.count()
        response = self.client.get('/teacher/notebooks/new/')
        self.assertContains(response, 'data-notebook-editor')
        self.assertContains(response, 'data-add-block="code"')
        self.assertEqual(Notebook.objects.count(), before)

    def test_create_notebook_and_render_interleaved_rich_text_code_image_file_video(self):
        new = Notebook(owner=self.teacher, title='Алгоритмы')
        code = 'if x < 2:\n    print("<script>example</script>")'
        rows = [{'kind': 'text', 'text': '<p><strong>Начало</strong> <u>важно</u></p>'},
                {'kind': 'code', 'text': code}, {'kind': 'image', 'image': self.image(), 'caption': 'Схема'},
                {'kind': 'text', 'text': '<h2>После картинки</h2>'},
                {'kind': 'file', 'file': SimpleUploadedFile('data.txt', b'42')},
                {'kind': 'video', 'video': SimpleUploadedFile('video.mp4', b'0123456789')}, {'kind': 'divider'}]
        response = self.client.post('/teacher/notebooks/new/', self.payload(rows, book=new))
        self.assertEqual(response.status_code, 302)
        book = Notebook.objects.get(title='Алгоритмы')
        self.assertEqual(book.owner, self.teacher)
        self.assertEqual(list(book.cells.values_list('kind', flat=True)), [row['kind'] for row in rows])
        self.assertEqual(book.cells.get(kind='code').text, code)
        self.assertTrue(MediaAsset.objects.filter(owner=self.teacher, kind='image').exists())
        self.client.force_login(self.student)
        page = self.client.get(f'/notebooks/{book.pk}/')
        self.assertContains(page, '<strong>Начало</strong>')
        self.assertContains(page, '<u>важно</u>')
        self.assertContains(page, '&lt;script&gt;example&lt;/script&gt;')
        self.assertNotContains(page, '<script>example</script>')
        self.assertNotContains(page, 'data-notebook-editor')
        self.assertLess(page.content.index('Схема'.encode()), page.content.index('После картинки'.encode()))

    def test_publish_empty_notebook_rejected_but_empty_draft_allowed(self):
        new = Notebook(owner=self.teacher, title='Пустой')
        response = self.client.post('/teacher/notebooks/new/', self.payload([], book=new))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'хотя бы одну ячейку')
        response = self.client.post('/teacher/notebooks/new/', self.payload([], book=new, published=''))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Notebook.objects.get(title='Пустой').published)

    def test_foreign_teacher_cannot_edit_archive_or_save_draft(self):
        self.client.force_login(self.other)
        for method in (self.client.get, self.client.post):
            self.assertEqual(method(self.url()).status_code, 404)
            self.assertEqual(method(f'/teacher/notebooks/{self.book.pk}/delete/').status_code, 404)
        response = self.client.post('/teacher/draft/?path='+self.url(), data=json.dumps({'data':{}, 'revision':0}), content_type='application/json')
        self.assertEqual(response.status_code, 404)
        self.book.refresh_from_db()
        self.assertIsNone(self.book.archived_at)

    def test_student_cannot_create_or_mutate_notebook(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.post('/teacher/notebooks/new/', {}).status_code, 302)
        self.assertEqual(self.client.post(self.url(), self.payload([self.existing()])).status_code, 302)
        self.assertEqual(self.client.post(f'/notebooks/{self.book.pk}/', {}).status_code, 405)
        self.assertEqual(self.client.get('/teacher/notebooks/').status_code, 403)

    def test_group_and_draft_publication_visibility_and_search(self):
        self.book.group = self.group
        self.book.save()
        self.client.force_login(self.outsider)
        self.assertNotContains(self.client.get('/notebooks/'), self.book.title)
        self.assertEqual(self.client.get(f'/notebooks/{self.book.pk}/').status_code, 404)
        self.client.force_login(self.student)
        self.assertContains(self.client.get('/notebooks/?q=Системы'), self.book.title)
        Notebook.objects.filter(pk=self.book.pk).update(published=False)
        self.assertEqual(self.client.get(f'/notebooks/{self.book.pk}/').status_code, 404)
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get('/teacher/notebooks/?status=draft'), self.book.title)

    def test_groups_in_editor_restricted_to_author(self):
        foreign = StudyGroup.objects.create(teacher=self.other, title='Чужая группа')
        response = self.client.post(self.url(), self.payload([self.existing()], group=foreign.pk))
        self.assertEqual(response.status_code, 200)
        self.assertIn('group', response.context['form'].errors)
        self.book.refresh_from_db()
        self.assertIsNone(self.book.group_id)

    def test_html_sanitized_on_save_and_again_on_render(self):
        self.save([{'id': self.cell.pk, 'kind': 'text', 'text': '<p onclick="bad()"><b>Важное</b><img src=x onerror=bad()></p>'}])
        self.cell.refresh_from_db()
        self.assertEqual(self.cell.text, '<p><strong>Важное</strong></p>')
        NotebookCell.objects.filter(pk=self.cell.pk).update(text='<svg onload="bad()"></svg><u>safe</u>')
        self.client.force_login(self.student)
        page = self.client.get(f'/notebooks/{self.book.pk}/')
        self.assertContains(page, '<u>safe</u>')
        self.assertNotContains(page, '<svg')

    def test_code_is_preserved_as_data_and_never_executed(self):
        code = 'raise RuntimeError("DO NOT RUN")\n    # отступы'
        self.save([{'id':self.cell.pk, 'kind':'code', 'text':code}])
        self.cell.refresh_from_db()
        self.assertEqual(self.cell.text, code)
        self.assertContains(self.client.get(f'/notebooks/{self.book.pk}/'), 'DO NOT RUN')

    def test_browser_newlines_keep_code_stable_across_round_trips(self):
        self.save([{'id':self.cell.pk, 'kind':'code', 'text':'for n in range(3):\r\n    print(n)\r\n'}])
        self.cell.refresh_from_db()
        self.assertEqual(self.cell.text, 'for n in range(3):\n    print(n)\n')
        revision = self.book.revision
        self.save([{'id':self.cell.pk, 'kind':'code', 'text':'for n in range(3):\n    print(n)\n'}])
        self.assertEqual(self.book.revision, revision)

    def test_forged_cell_id_copy_source_and_missing_ids_rejected(self):
        foreign = Notebook.objects.create(owner=self.other, title='Чужой')
        cell = NotebookCell.objects.create(notebook=foreign, kind='image', image=self.image())
        for rows in ([{'id': cell.pk, 'kind':'text', 'text':'Затереть'}],
                     [self.existing(), {'kind':'image', 'copy_from':cell.pk}], []):
            response = self.client.post(self.url(), self.payload(rows))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['blocks'].errors or response.context['blocks'].non_form_errors())
        self.cell.refresh_from_db()
        self.assertEqual(self.cell.text, '<h2>Основание системы</h2>')

    def test_duplicate_existing_media_and_reorder_cells(self):
        photo = NotebookCell.objects.create(notebook=self.book, kind='image', image=self.image())
        rows = [{'id':photo.pk, 'kind':'image'}, self.existing(), {'kind':'image', 'copy_from':photo.pk}]
        self.save(rows, **{'blocks-1-position':3, 'blocks-2-position':2})
        self.assertEqual(list(self.book.cells.values_list('kind', flat=True)), ['image','image','text'])
        self.assertEqual(len(set(self.book.cells.filter(kind='image').values_list('image',flat=True))), 1)

    def test_switch_type_clears_inactive_media(self):
        photo = NotebookCell.objects.create(notebook=self.book, kind='image', image=self.image())
        self.save([self.existing(), {'id':photo.pk, 'kind':'code', 'text':'print(42)'}])
        photo.refresh_from_db()
        self.assertFalse(photo.image)
        self.assertEqual(self.client.get(f'/files/notebook-cell/{photo.pk}/').status_code, 404)

    def test_stale_editor_does_not_overwrite_saved_content(self):
        stale = self.payload([self.existing()], title='Устаревшая вкладка')
        self.save([{'id':self.cell.pk, 'kind':'text', 'text':'Новые данные'}], title='Новое название')
        response = self.client.post(self.url(), stale)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'другой вкладке')
        self.book.refresh_from_db(); self.cell.refresh_from_db()
        self.assertEqual(self.book.title, 'Новое название')
        self.assertEqual(self.cell.text, 'Новые данные')

    def test_noop_save_preserves_progress_and_content_edit_invalidates_all_linked_steps(self):
        extra = clone_step(self.step, title='Ещё один слайд')
        for step in (self.step, extra):
            StepProgress.objects.create(user=self.student, step=step, completed=True, revision=step.revision)
        LessonProgress.objects.create(user=self.student, lesson=self.lesson, passed=True)
        StepSubmission.objects.create(user=self.student, step=self.step, correct=True, answer='История')
        self.save([self.existing()])
        self.step.refresh_from_db()
        self.assertEqual(self.step.revision, 1)
        self.assertEqual(current_progress(self.student).count(), 2)
        self.save([{'id':self.cell.pk, 'kind':'text', 'text':'Новое объяснение'}])
        self.step.refresh_from_db(); extra.refresh_from_db()
        self.assertEqual(self.step.revision, 2)
        self.assertEqual(extra.revision, 2)
        self.assertEqual(current_progress(self.student).count(), 0)
        self.assertFalse(LessonProgress.objects.get(lesson=self.lesson).passed)
        self.assertEqual(StepSubmission.objects.get(step=self.step).answer, 'История')

    def test_unpublish_notebook_unpublishes_linked_slides(self):
        self.save([self.existing()], published='')
        self.step.refresh_from_db()
        self.assertFalse(self.step.published)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(f'/learn/{self.step.pk}/').status_code, 404)

    def test_editing_notebook_also_invalidates_steps_in_archived_courses(self):
        archive(self.teacher, self.course)
        self.save([{'id':self.cell.pk, 'kind':'text', 'text':'Новая версия материала'}])
        self.step.refresh_from_db()
        self.assertEqual(self.step.revision, 2)

    def test_course_embedding_and_media_use_course_access(self):
        # Standalone group restrictions can differ from the course's audience.
        foreign_group = StudyGroup.objects.create(teacher=self.teacher, title='Другая группа')
        self.book.group = foreign_group; self.book.save()
        photo = NotebookCell.objects.create(notebook=self.book, kind='image', image=self.image())
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(f'/notebooks/{self.book.pk}/').status_code, 404)
        page = self.client.get(f'/learn/{self.step.pk}/')
        self.assertContains(page, 'Основание системы')
        self.assertContains(page, f'&step={self.step.pk}')
        self.assertEqual(self.client.get(f'/notebooks/{self.book.pk}/?step={self.step.pk}').status_code, 200)
        self.assertEqual(self.client.get(f'/files/notebook-cell/{photo.pk}/').status_code, 404)
        response = self.client.get(f'/files/notebook-cell/{photo.pk}/?step={self.step.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertIn('private', response['Cache-Control'])
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        response.close()
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(f'/files/notebook-cell/{photo.pk}/?step={self.step.pk}').status_code, 403)

    def test_course_context_cannot_be_forged_and_checks_sequential_progress(self):
        wrong = Step.objects.create(lesson=self.lesson, title='Другой', body='Другой материал', position=0)
        self.client.force_login(self.student)
        for value in (wrong.pk, 'bad', '999999999999999999999999999'):
            self.assertEqual(self.client.get(f'/notebooks/{self.book.pk}/?step={value}').status_code, 404)
        self.course.sequential = True; self.course.save()
        self.assertEqual(self.client.get(f'/notebooks/{self.book.pk}/?step={self.step.pk}').status_code, 403)
        StepProgress.objects.create(user=self.student, step=wrong, completed=True, revision=wrong.revision)
        self.assertEqual(self.client.get(f'/notebooks/{self.book.pk}/?step={self.step.pk}').status_code, 200)

    def step_payload(self, book):
        return {'title':'Конспект и практика', 'kind':'answer', 'answer':'42', 'published':'on', 'points':1, 'max_attempts':0,
                'edit_version':self.step.edit_version, 'blocks-TOTAL_FORMS':2, 'blocks-INITIAL_FORMS':1,
                'blocks-0-id':self.link.pk, 'blocks-0-kind':'notebook', 'blocks-0-position':1, 'blocks-0-notebook':book.pk,
                'blocks-1-kind':'text', 'blocks-1-position':2, 'blocks-1-text':'Что выведет код?'}

    def test_notebook_combines_with_assessment_on_one_slide(self):
        response = self.client.post(f'/teacher/lessons/{self.lesson.pk}/steps/{self.step.pk}/', self.step_payload(self.book))
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.student)
        page = self.client.get(f'/learn/{self.step.pk}/')
        self.assertContains(page, 'Основание системы')
        self.assertContains(page, 'Что выведет код?')
        self.client.post(f'/learn/{self.step.pk}/', {'answer':'42'})
        self.assertTrue(StepSubmission.objects.get(step=self.step).correct)

    def test_foreign_or_draft_notebook_cannot_publish_course_step(self):
        foreign = Notebook.objects.create(owner=self.other, title='Чужой', published=True)
        response = self.client.post(f'/teacher/lessons/{self.lesson.pk}/steps/{self.step.pk}/', self.step_payload(foreign))
        self.assertEqual(response.status_code, 200)
        self.assertIn('notebook', response.context['blocks'].forms[0].errors)
        Notebook.objects.filter(pk=self.book.pk).update(published=False)
        response = self.client.post(f'/teacher/lessons/{self.lesson.pk}/steps/{self.step.pk}/', self.step_payload(self.book))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Опубликуйте выбранные конспекты')
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(f'/learn/{self.step.pk}/').status_code, 404)

    def test_model_requires_notebook_block_for_published_notebook_slide(self):
        step = Step(lesson=self.lesson, title='Пустой', kind='notebook', published=True, use_blocks=True)
        with self.assertRaises(ValidationError):
            step.clean()

    def test_archive_confirmation_restore_and_old_editor_conflict(self):
        stale = self.payload([self.existing()])
        url = f'/teacher/notebooks/{self.book.pk}/delete/'
        self.assertContains(self.client.get(url), 'Удалить конспект?')
        self.book.refresh_from_db(); self.assertIsNone(self.book.archived_at)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.step.refresh_from_db(); self.assertFalse(self.step.published)
        self.assertTrue(NotebookCell.objects.filter(pk=self.cell.pk).exists())
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(f'/notebooks/{self.book.pk}/').status_code, 404)
        self.client.force_login(self.teacher)
        entry = TrashEntry.objects.get(model='learning.notebook')
        self.assertEqual(self.client.post('/teacher/trash/', {'pk':entry.pk}).status_code, 302)
        self.book.refresh_from_db(); self.assertFalse(self.book.published)
        self.assertEqual(self.client.post(self.url(), stale).status_code, 200)
        self.book.refresh_from_db(); self.assertFalse(self.book.published)

    def test_archive_is_idempotent_and_rechecks_restore_ownership(self):
        self.assertTrue(archive(self.teacher, self.book))
        self.assertFalse(archive(self.teacher, self.book))
        entry = TrashEntry.objects.get(model='learning.notebook')
        Notebook.all_objects.filter(pk=self.book.pk).update(owner=self.other)
        self.assertEqual(self.client.post('/teacher/trash/', {'pk':entry.pk}).status_code, 403)

    def test_video_range_and_invalid_upload_validation(self):
        self.save([self.existing(), {'kind':'video', 'video':SimpleUploadedFile('video.mp4', b'0123456789')}])
        video = self.book.cells.get(kind='video')
        self.client.force_login(self.student)
        response = self.client.get(f'/files/notebook-cell/{video.pk}/', HTTP_RANGE='bytes=2-5')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(b''.join(response.streaming_content), b'2345')
        self.client.force_login(self.teacher)
        response = self.client.post(self.url(), self.payload([self.existing(), {'id':video.pk, 'kind':'image', 'image':SimpleUploadedFile('x.svg', b'<svg/>')}]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['blocks'].forms[1].errors)

    def test_cell_limits_and_csrf(self):
        rows = [self.existing()] + [{'kind':'code', 'text':'print(1)'}]*40
        response = self.client.post(self.url(), self.payload(rows))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['blocks'].non_form_errors())
        response = self.client.post(self.url(), self.payload([{'id':self.cell.pk, 'kind':'code', 'text':'a'*100001}]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('text', response.context['blocks'].forms[0].errors)
        client = Client(enforce_csrf_checks=True); client.force_login(self.teacher)
        self.assertEqual(client.post(self.url(), self.payload([self.existing()])).status_code, 403)

    def test_private_draft_saved_and_cleared_after_successful_save(self):
        data = {'data':{'title':['Мой черновик'], 'blocks-0-text':['<p>Заметка</p>']}, 'revision':0}
        response = self.client.post('/teacher/draft/?path='+self.url(), json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(EditorDraft.objects.filter(user=self.teacher, path=self.url()).exists())
        self.client.force_login(self.other)
        self.assertEqual(self.client.get('/teacher/draft/?path='+self.url()).status_code, 404)
        self.client.force_login(self.teacher)
        self.save([self.existing()])
        self.assertFalse(EditorDraft.objects.filter(user=self.teacher, path=self.url()).exists())

    def test_superuser_can_edit_foreign_notebook_and_django_admin_redirects_to_editor(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        response = self.client.get(f'/admin/learning/notebook/{self.book.pk}/change/')
        self.assertRedirects(response, self.url())

    def test_portable_backup_keeps_archived_notebooks_cells_media_and_course_reference(self):
        photo = NotebookCell.objects.create(notebook=self.book, kind='image', image=self.image())
        archive(self.teacher, self.book)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'notebook-backup.zip'
            call_command('backup_platform', str(path), stdout=io.StringIO())
            call_command('backup_platform', str(path), verify=True, stdout=io.StringIO())
            with zipfile.ZipFile(path) as backup:
                data = json.loads(backup.read('data.json'))
                book = next(row for row in data if row['model']=='learning.notebook' and row['pk']==self.book.pk)
                cell = next(row for row in data if row['model']=='learning.notebookcell' and row['pk']==photo.pk)
                link = next(row for row in data if row['model']=='learning.stepcontentblock' and row['pk']==self.link.pk)
                self.assertTrue(book['fields']['archived_at'])
                self.assertEqual(cell['fields']['notebook'], self.book.pk)
                self.assertEqual(link['fields']['notebook'], self.book.pk)
                self.assertTrue(backup.read('media/'+photo.image.name).startswith(b'\x89PNG'))
