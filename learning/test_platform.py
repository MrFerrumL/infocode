import io
import json
import tempfile
import zipfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from PIL import Image
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from .models import *
from .security import form_token
from .services import create_variant, finish_attempt
from .analytics import aggregate_students
from .platform_views import archive


class PlatformTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user('teacher', is_staff=True)
        cls.other_teacher = User.objects.create_user('other_teacher', is_staff=True)
        cls.student = User.objects.create_user('student', first_name='Аня')
        cls.other = User.objects.create_user('stranger')
        StudentAssignment.objects.create(teacher=cls.teacher, student=cls.student)
        cls.group = StudyGroup.objects.create(title='Группа', teacher=cls.teacher)
        cls.group.students.add(cls.student)
        cls.topic = Topic.objects.create(number=1, title='Графы')
        cls.task = Task.objects.create(topic=cls.topic, title='Задача', statement='Сколько?', answer='42')
        cls.course = Course.objects.create(owner=cls.teacher, title='Курс', published=True)
        cls.section = Section.objects.create(course=cls.course, title='Раздел')
        cls.lesson = Lesson.objects.create(section=cls.section, title='Урок', published=True)
        cls.step = Step.objects.create(lesson=cls.lesson, title='Теория', kind='text', body='Материал')
        Enrollment.objects.create(user=cls.student, course=cls.course)

    def setUp(self):
        self.client.force_login(self.teacher)
        self.tmp = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.tmp.name)
        self.override.enable()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.override.disable)

    def image(self, width=10):
        out = io.BytesIO()
        Image.new('RGB', (width, 10), 'white').save(out, format='PNG')
        return SimpleUploadedFile('picture.png', out.getvalue(), content_type='image/png')

    def work_data(self, **changes):
        now=timezone.localtime()
        data={'title':'Обязательная контрольная','kind':'required_test','target':'variant',
              'students':[self.student.pk], 'task_ids':str(self.task.pk), 'count':1, 'duration':60,
              'starts_at':(now-timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
              'due_at':(now+timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M'),
              'max_attempts':1,'pass_percent':100}
        data.update(changes)
        return data

    def work(self, **changes):
        response=self.client.post('/teacher/assignments/new/',self.work_data(**changes))
        self.assertEqual(response.status_code,302, getattr(response,'context',None) and response.context['form'].errors)
        return LearningAssignment.objects.latest('pk')

    def test_teacher_and_student_pages_render(self):
        for path in ['/teacher/analytics/','/teacher/students/','/teacher/students/%s/'%self.student.pk,
                     '/teacher/journal/','/teacher/assignments/new/','/teacher/groups/','/teacher/library/',
                     '/teacher/trash/','/teacher/audit/','/teacher/import/','/teacher/duplicates/']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code,200)
        self.client.force_login(self.student)
        for path in ['/assignments/','/notifications/']:
            self.assertEqual(self.client.get(path).status_code,200)
        self.assertEqual(self.client.get('/teacher/analytics/').status_code,302)

    def test_kr_snapshot_recipients_and_notification(self):
        work=self.work()
        self.assertTrue(work.variant.assigned_only)
        self.assertEqual(work.variant.items.get().answer,'42')
        self.task.answer='99';self.task.save()
        self.assertEqual(work.variant.items.get().answer,'42')
        self.assertTrue(Notification.objects.filter(user=self.student).exists())
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/variants/{work.variant_id}/').status_code,404)
        self.assertEqual(self.client.get(f'/assignments/{work.pk}/').status_code,403)

    def test_kr_attempt_limit_enforced_through_regular_variant_route(self):
        work=self.work()
        self.client.force_login(self.student)
        self.client.post(f'/variants/{work.variant_id}/start/')
        attempt=ExamAttempt.objects.get(user=self.student)
        self.client.post(f'/exam/{attempt.pk}/',{'action':'submit'})
        response=self.client.post(f'/variants/{work.variant_id}/start/')
        self.assertRedirects(response,f'/assignments/{work.pk}/')
        self.assertEqual(ExamAttempt.objects.count(),1)

    def test_kr_future_and_deadline(self):
        now=timezone.localtime()
        work=self.work(starts_at=(now+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'))
        self.client.force_login(self.student)
        self.client.post(f'/variants/{work.variant_id}/start/')
        self.assertFalse(ExamAttempt.objects.exists())
        work.starts_at=timezone.now()-timedelta(hours=2);work.due_at=timezone.now()-timedelta(hours=1);work.save()
        self.client.post(f'/variants/{work.variant_id}/start/')
        self.assertFalse(ExamAttempt.objects.exists())

    def test_kr_deadline_clamps_timer_and_expiry_does_not_accept_new_answers(self):
        now=timezone.localtime()
        work=self.work(due_at=(now+timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M'))
        self.client.force_login(self.student)
        self.client.post(f'/variants/{work.variant_id}/start/')
        attempt=ExamAttempt.objects.get()
        self.assertEqual(attempt.deadline,work.due_at)
        ExamAttempt.objects.filter(pk=attempt.pk).update(deadline=timezone.now()-timedelta(seconds=1))
        item=work.variant.items.get()
        self.client.post(f'/exam/{attempt.pk}/',{'action':'submit',f'answer_{item.pk}':'42'})
        attempt.refresh_from_db()
        self.assertEqual(attempt.score,0)

    def test_kr_pass_stops_further_attempts(self):
        work=self.work(max_attempts=3)
        self.client.force_login(self.student)
        self.client.post(f'/variants/{work.variant_id}/start/')
        attempt=ExamAttempt.objects.get()
        item=work.variant.items.get()
        self.client.post(f'/exam/{attempt.pk}/',{'action':'submit',f'answer_{item.pk}':'42'})
        recipient=work.recipients.get()
        self.assertIsNotNone(recipient.completed_at)
        self.client.post(f'/variants/{work.variant_id}/start/')
        self.assertEqual(ExamAttempt.objects.count(),1)

    def test_kr_extra_attempt_and_closed_assignment(self):
        work=self.work()
        r=work.recipients.get()
        self.client.force_login(self.student)
        self.client.post(f'/variants/{work.variant_id}/start/')
        attempt=ExamAttempt.objects.get()
        self.client.post(f'/exam/{attempt.pk}/',{'action':'submit'})
        self.client.force_login(self.teacher)
        self.client.post(f'/assignments/{work.pk}/',{'action':'extra','recipient':r.pk})
        self.client.force_login(self.student)
        self.client.post(f'/variants/{work.variant_id}/start/')
        self.assertEqual(ExamAttempt.objects.count(),2)
        work.active=False;work.save()
        active=ExamAttempt.objects.get(submitted_at=None)
        self.assertRedirects(self.client.get(f'/exam/{active.pk}/'),f'/assignments/{work.pk}/')

    def test_kr_cannot_assign_foreign_student_or_group(self):
        r=self.client.post('/teacher/assignments/new/',self.work_data(students=[self.other.pk]))
        self.assertEqual(r.status_code,200)
        self.assertFalse(LearningAssignment.objects.exists())
        foreign=StudyGroup.objects.create(title='Чужая',teacher=self.other_teacher)
        r=self.client.post('/teacher/assignments/new/',self.work_data(group=foreign.pk))
        self.assertEqual(r.status_code,200)
        self.assertFalse(LearningAssignment.objects.exists())

    def test_create_assignment_idempotent(self):
        data=self.work_data(submit_token=form_token(self.teacher,'assignment-new'))
        self.client.post('/teacher/assignments/new/',data)
        self.client.post('/teacher/assignments/new/',data)
        self.assertEqual(LearningAssignment.objects.count(),1)

    def test_required_test_must_use_variant(self):
        r=self.client.post('/teacher/assignments/new/',self.work_data(target='manual'))
        self.assertEqual(r.status_code,200)
        self.assertFalse(LearningAssignment.objects.exists())

    def test_private_manual_file_review_return_and_resubmit(self):
        work=self.work(kind='homework',target='manual')
        self.client.force_login(self.student)
        self.client.post(f'/assignments/{work.pk}/',{'text':'Моё решение','image':self.image(),'file':SimpleUploadedFile('answer.py',b'print(42)')})
        s=HomeworkSubmission.objects.get()
        self.client.post(f'/assignments/{work.pk}/',{'text':'Дубль во время проверки'})
        self.assertEqual(HomeworkSubmission.objects.count(),1)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/files/homework-file/{s.pk}/').status_code,404)
        self.client.force_login(self.other_teacher)
        self.assertEqual(self.client.get(f'/teacher/review/{s.pk}/').status_code,403)
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(f'/teacher/review/{s.pk}/').status_code,200)
        self.client.post(f'/teacher/review/{s.pk}/',{'status':'returned','feedback':'Объясни цикл','score':30})
        self.client.force_login(self.student)
        self.client.post(f'/assignments/{work.pk}/',{'text':'Исправлено'})
        self.assertEqual(HomeworkSubmission.objects.count(),2)
        revised=HomeworkSubmission.objects.latest('pk')
        self.client.force_login(self.teacher)
        self.client.post(f'/teacher/review/{revised.pk}/',{'status':'accepted','score':100})
        self.assertIsNotNone(work.recipients.get().completed_at)
        self.assertContains(self.client.get('/teacher/journal/'),'Зачтено')

    def test_lesson_assignment_checks_real_progress(self):
        work=self.work(kind='homework',target='lesson',lesson=self.lesson.pk)
        self.client.force_login(self.student)
        self.client.post(f'/assignments/{work.pk}/')
        self.assertIsNone(work.recipients.get().completed_at)
        self.client.post(f'/learn/{self.step.pk}/',{'action':'complete'})
        self.client.post(f'/assignments/{work.pk}/')
        self.assertIsNotNone(work.recipients.get().completed_at)

    def test_practice_duplicate_token_and_wrong_user_token(self):
        self.client.force_login(self.student)
        token=form_token(self.student,f'task:{self.task.pk}')
        data={'answer':'42','submit_token':token}
        self.client.post(f'/tasks/{self.task.pk}/',data)
        self.client.post(f'/tasks/{self.task.pk}/',data)
        self.assertEqual(Practice.objects.count(),1)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(f'/tasks/{self.task.pk}/',data).status_code,403)

    def test_numeric_tolerance_and_duplicate_submission(self):
        step=Step.objects.create(lesson=self.lesson,title='Число',kind='numeric',answer='3.14',tolerance=Decimal('.01'),max_attempts=2)
        self.client.force_login(self.student)
        data={'answer':'3,145','submit_token':form_token(self.student,f'step:{step.pk}')}
        self.client.post(f'/learn/{step.pk}/',data)
        self.client.post(f'/learn/{step.pk}/',data)
        self.assertTrue(StepProgress.objects.get(step=step).completed)
        self.assertEqual(StepSubmission.objects.filter(step=step).count(),1)

    def test_numeric_rejects_nan(self):
        step=Step.objects.create(lesson=self.lesson,title='Число',kind='numeric',answer='3.14')
        self.client.force_login(self.student)
        self.client.post(f'/learn/{step.pk}/',{'answer':'NaN'})
        self.assertFalse(StepProgress.objects.get(step=step).completed)

    def test_gaps_threshold_and_no_answer_leak(self):
        step=Step.objects.create(lesson=self.lesson,title='Пропуски',kind='gaps',pairs=[{'prompt':'A','answer':'SECRET_A'},{'prompt':'B','answer':'SECRET_B'}],pass_percent=50)
        self.client.force_login(self.student)
        self.assertNotContains(self.client.get(f'/learn/{step.pk}/'),'SECRET_A')
        self.client.post(f'/learn/{step.pk}/',{'pair_0':'SECRET_A','pair_1':'bad'})
        self.assertTrue(StepProgress.objects.get(step=step).completed)

    def test_matching_and_cannot_skip_with_complete(self):
        step=Step.objects.create(lesson=self.lesson,title='Пары',kind='matching',pairs=[{'prompt':'A','answer':'Alpha'},{'prompt':'B','answer':'Beta'}])
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(f'/learn/{step.pk}/',{'action':'complete'}).status_code,403)
        self.client.post(f'/learn/{step.pk}/',{'pair_0':'Beta','pair_1':'Alpha'})
        self.assertFalse(StepProgress.objects.get(step=step).completed)
        self.client.post(f'/learn/{step.pk}/',{'pair_0':'Alpha','pair_1':'Beta'})
        self.assertTrue(StepProgress.objects.get(step=step).completed)

    def test_sequential_course_guards_direct_step_and_media(self):
        self.course.sequential=True;self.course.save()
        second=Step.objects.create(lesson=self.lesson,title='Дальше',kind='file',file=SimpleUploadedFile('data.txt',b'example'),position=2)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(f'/learn/{second.pk}/').status_code,403)
        self.assertEqual(self.client.get(f'/files/step-file/{second.pk}/').status_code,403)
        self.client.post(f'/learn/{self.step.pk}/',{'action':'complete'})
        self.assertEqual(self.client.get(f'/learn/{second.pk}/').status_code,200)

    def test_video_resume_private_and_revisioned(self):
        step=Step.objects.create(lesson=self.lesson,title='Видео',kind='video',video=SimpleUploadedFile('video.mp4',b'fake'))
        self.client.force_login(self.student)
        self.client.post(f'/learn/{step.pk}/position/',{'seconds':42})
        self.assertEqual(self.client.get(f'/learn/{step.pk}/position/').json()['seconds'],42)
        step.revision+=1;step.save()
        self.assertEqual(self.client.get(f'/learn/{step.pk}/position/').json()['seconds'],0)
        self.assertEqual(self.client.post(f'/learn/{step.pk}/position/',{'seconds':'NaN'}).status_code,400)

    def test_server_draft_ownership_conflicts_and_clear_on_save(self):
        url=f'/teacher/draft/?path=/teacher/tasks/{self.task.pk}/edit/'
        data={'revision':0,'data':{'title':['Не опубликовано']}}
        self.assertEqual(self.client.post(url,json.dumps(data),content_type='application/json').status_code,200)
        self.task.refresh_from_db();self.assertEqual(self.task.title,'Задача')
        self.assertEqual(self.client.post(url,json.dumps(data),content_type='application/json').status_code,409)
        self.client.force_login(self.other_teacher)
        self.assertEqual(self.client.get(url).json()['data'],{})
        self.client.force_login(self.teacher)
        self.client.post(f'/teacher/tasks/{self.task.pk}/edit/',{'title':'Сохранено','topic':self.topic.pk,'answer':'42','difficulty':'easy'})
        self.assertFalse(EditorDraft.objects.filter(user=self.teacher).exists())

    def test_reorder_checks_exact_siblings_and_owner(self):
        second=Step.objects.create(lesson=self.lesson,title='Второй',kind='text',body='Текст',position=2)
        data={'kind':'step','parent':self.lesson.pk,'ids':[second.pk,self.step.pk]}
        self.assertEqual(self.client.post('/teacher/reorder/',data).status_code,200)
        self.assertEqual(list(self.lesson.steps.values_list('pk',flat=True)),[second.pk,self.step.pk])
        self.assertEqual(self.client.post('/teacher/reorder/',{**data,'ids':[second.pk,second.pk]}).status_code,409)
        self.client.force_login(self.other_teacher)
        self.assertEqual(self.client.post('/teacher/reorder/',data).status_code,404)

    def test_media_library_type_and_scope(self):
        response=self.client.post('/teacher/library/',{'title':'Картинка','kind':'image','file':self.image()})
        self.assertEqual(response.status_code,302)
        asset=MediaAsset.objects.get()
        preview=self.client.get(f'/files/library/{asset.pk}/?preview=1')
        self.assertEqual(preview.status_code,200);preview.close()
        self.client.force_login(self.other_teacher)
        self.assertEqual(self.client.get(f'/files/library/{asset.pk}/').status_code,404)
        self.client.force_login(self.teacher)
        self.client.post('/teacher/library/',{'title':'Плохой','kind':'image','file':SimpleUploadedFile('evil.png',b'html')})
        self.assertEqual(MediaAsset.objects.count(),1)

    def test_media_library_reuse_in_block(self):
        asset=MediaAsset.objects.create(owner=self.teacher,title='Фото',kind='image',file=self.image())
        r=self.client.post(f'/teacher/tasks/{self.task.pk}/blocks/new/',{'kind':'image','asset':asset.pk})
        self.assertEqual(r.status_code,302)
        self.assertEqual(self.task.content_blocks.get().image.name,asset.file.name)

    def test_archive_restores_draft_and_preserves_snapshot(self):
        variant=create_variant(creator=self.teacher,title='Issued',topics=[self.topic])
        self.client.post('/teacher/bulk/',{'selected':[self.task.pk],'action':'archive'})
        self.assertFalse(Task.objects.filter(pk=self.task.pk).exists())
        self.assertTrue(Task.all_objects.filter(pk=self.task.pk).exists())
        self.assertEqual(variant.items.get().statement,'Сколько?')
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(f'/tasks/{self.task.pk}/').status_code,404)
        self.client.force_login(self.teacher)
        self.client.post('/teacher/trash/',{'pk':TrashEntry.objects.get().pk})
        self.assertFalse(Task.objects.get(pk=self.task.pk).published)

    def test_archived_parent_hides_steps_and_files(self):
        self.client.post(f'/teacher/structure/section/{self.section.pk}/',{'action':'archive'})
        self.assertFalse(Step.objects.filter(pk=self.step.pk).exists())
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(f'/learn/{self.step.pk}/').status_code,404)

    def test_csv_preview_then_atomic_commit_and_no_duplicate_import(self):
        payload='number;title;answer;statement;difficulty\n1;CSV задача;7;Условие;ege'.encode()
        self.client.post('/teacher/import/',{'file':SimpleUploadedFile('tasks.csv',payload)})
        self.assertEqual(Task.objects.count(),1)
        batch=ImportBatch.objects.get()
        self.assertFalse(batch.errors)
        self.client.post('/teacher/import/',{'batch':batch.pk})
        self.client.post('/teacher/import/',{'batch':batch.pk})
        self.assertEqual(Task.objects.count(),2)
        self.assertFalse(Task.objects.get(title='CSV задача').published)

    def test_csv_invalid_row_prevents_entire_import_and_owner_access(self):
        self.client.post('/teacher/import/',{'file':SimpleUploadedFile('tasks.csv',b'number,title,answer\n1,Okay,42\n99,No,5')})
        batch=ImportBatch.objects.get()
        self.assertTrue(batch.errors)
        self.client.post('/teacher/import/',{'batch':batch.pk})
        self.assertEqual(Task.objects.count(),1)
        self.client.force_login(self.other_teacher)
        self.assertEqual(self.client.post('/teacher/import/',{'batch':batch.pk}).status_code,404)

    def test_invite_limits_expiry_and_idempotency(self):
        invite=GroupInvite.objects.create(teacher=self.teacher,group=self.group,expires_at=timezone.now()+timedelta(days=1),max_uses=1)
        self.client.force_login(self.other)
        self.client.get(f'/join/{invite.token}/')
        self.assertFalse(StudentAssignment.objects.filter(student=self.other).exists())
        self.client.post(f'/join/{invite.token}/')
        self.client.post(f'/join/{invite.token}/')
        invite.refresh_from_db();self.assertEqual(invite.uses,1)
        self.assertTrue(StudentAssignment.objects.filter(student=self.other,teacher=self.teacher).exists())
        self.client.force_login(self.student)
        self.client.post(f'/join/{invite.token}/')
        self.assertEqual(InviteUse.objects.count(),1)

    def test_notifications_are_private_and_read_post_only(self):
        note=Notification.objects.create(user=self.student,title='Приватное')
        self.assertNotContains(self.client.get('/notifications/'),'Приватное')
        self.client.force_login(self.student)
        self.assertContains(self.client.get('/notifications/'),'Приватное')
        note.refresh_from_db();self.assertIsNone(note.read_at)
        self.client.post('/notifications/',{'pk':note.pk})
        note.refresh_from_db();self.assertIsNotNone(note.read_at)

    def test_teacher_notes_are_private_between_teachers(self):
        self.client.post(f'/teacher/students/{self.student.pk}/',{'note':'PRIVATE_NOTE'})
        StudentAssignment.objects.create(teacher=self.other_teacher,student=self.student)
        self.client.force_login(self.other_teacher)
        self.assertNotContains(self.client.get(f'/teacher/students/{self.student.pk}/'),'PRIVATE_NOTE')
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(f'/teacher/students/{self.student.pk}/').status_code,302)

    def test_first_attempts_across_period_and_bounded_queries(self):
        first=Practice.objects.create(user=self.student,task=self.task,answer='wrong',correct=False)
        Practice.objects.filter(pk=first.pk).update(created_at=timezone.now()-timedelta(days=40))
        Practice.objects.create(user=self.student,task=self.task,answer='42',correct=True)
        result,_=aggregate_students([self.student],timezone.now()-timedelta(days=1),timezone.now()+timedelta(days=1))
        self.assertEqual(result[0].first_total,0)
        self.assertEqual(result[0].repeats,1)
        self.assertEqual(result[0].accuracy,100)
        with CaptureQueriesContext(connection) as one:
            aggregate_students([self.student])
        many=[User.objects.create_user(f'count_{n}') for n in range(10)]
        with CaptureQueriesContext(connection) as eleven:
            aggregate_students([self.student]+many)
        self.assertEqual(len(one),len(eleven))
        self.assertLessEqual(len(eleven),10)

    def test_audit_records_changes_without_mixing_teachers(self):
        self.client.post('/teacher/bulk/',{'selected':[self.task.pk],'action':'difficulty','difficulty':'grave'})
        e=AuditEvent.objects.filter(object_id=self.task.pk,model='learning.task').latest('pk')
        self.assertEqual(e.changes['difficulty'],{'before':'easy','after':'grave'})
        self.client.force_login(self.other_teacher)
        self.assertNotContains(self.client.get('/teacher/audit/'),'grave')

    @override_settings(LOGIN_RATE_LIMIT=2)
    def test_login_rate_limit(self):
        self.client.logout()
        for _ in range(2):
            self.assertEqual(self.client.post('/login/',{'username':'missing','password':'wrong'}).status_code,200)
        response=self.client.post('/login/',{'username':'missing','password':'wrong'})
        self.assertEqual(response.status_code,429)
        self.assertIn('Retry-After',response)

    def test_backup_includes_archived_data_and_detects_tampering(self):
        archive(self.teacher,self.task)
        path=Path(self.tmp.name).parent/(Path(self.tmp.name).name+'-backup.zip')
        self.addCleanup(path.unlink,missing_ok=True)
        call_command('backup_platform',str(path),stdout=io.StringIO())
        with zipfile.ZipFile(path) as z:
            rows=json.loads(z.read('data.json'))
        self.assertTrue(any(r['model']=='learning.task' and r['fields']['title']=='Задача' for r in rows))
        with zipfile.ZipFile(path,'a') as z:
            z.writestr('data.json','[]')
        with self.assertRaises(CommandError):
            call_command('backup_platform',str(path),verify=True,stdout=io.StringIO())
