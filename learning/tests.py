import tempfile
from datetime import timedelta
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, Client, override_settings
from django.utils import timezone
from .models import Topic, Task, Variant, VariantItem, ExamAttempt, ExamAnswer, Practice, Bookmark, StudyGroup, Lesson, LessonProgress, LessonFile, QuizQuestion
from .models import Course, Step, Enrollment, StepProgress, StudentAssignment
from .services import create_variant, normalize, check_answer


class PlatformTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_superuser('teacher', '', 'StrongTestPassword1!')
        cls.student = User.objects.create_user('student', password='StrongTestPassword1!')
        cls.other = User.objects.create_user('other', password='StrongTestPassword1!')
        StudentAssignment.objects.create(teacher=cls.teacher, student=cls.student)
        with tempfile.TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            call_command('seed_demo', owner='teacher', verbosity=0)
        cls.task = Task.objects.first()
        cls.variant = Variant.objects.filter(kind='exam').first()

    def setUp(self):
        self.client.force_login(self.student)
        Enrollment.objects.get_or_create(user=self.student, course=Course.objects.first())

    def start(self):
        self.client.post(f'/variants/{self.variant.pk}/start/')
        return ExamAttempt.objects.get(user=self.student, variant=self.variant, submitted_at=None)

    def test_all_student_pages_render(self):
        for path in ['/', '/catalog/', '/catalog/?number=27', '/catalog/?q=граф', '/catalog/?q=1', '/catalog/?state=saved', '/catalog/?state=mistakes', '/variants/', '/tests/', '/course/', '/stats/', '/password/', f'/tasks/{self.task.pk}/', f'/variants/{self.variant.pk}/']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        for lesson in Lesson.objects.all():
            self.assertEqual(self.client.get(f'/lessons/{lesson.pk}/').status_code, 200)

    def test_anonymous_needs_login(self):
        self.client.logout()
        for path in ['/', '/catalog/', '/course/', '/teacher/', '/files/video/1/']:
            self.assertEqual(self.client.get(path).status_code, 302)

    def test_student_cannot_use_teacher_actions(self):
        for path in ['/teacher/', '/teacher/variants/new/', '/teacher/tasks/new/', f'/teacher/variants/{self.variant.pk}/publish/', f'/teacher/variants/{self.variant.pk}/print/', '/teacher/?export=csv']:
            self.assertEqual(self.client.post(path).status_code, 302)

    def test_teacher_pages_render(self):
        self.client.force_login(self.teacher)
        for path in ['/teacher/', '/teacher/variants/new/', '/teacher/tasks/new/', f'/teacher/variants/{self.variant.pk}/print/', f'/teacher/variants/{self.variant.pk}/print/?answers=1', '/admin/', '/admin/learning/task/', '/admin/learning/lesson/']:
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_registration_does_not_grant_staff(self):
        self.client.logout()
        response = self.client.post('/register/', {'first_name':'Новый', 'username':'newstudent', 'password1':'UniqueStudentPass319!', 'password2':'UniqueStudentPass319!', 'is_staff':'1'})
        self.assertEqual(response.status_code,302)
        self.assertFalse(User.objects.get(username='newstudent').is_staff)

    def test_answer_normalization_preserves_leading_zero(self):
        self.assertTrue(check_answer(' 12\n34 ', '12 34'))
        self.assertFalse(check_answer('1', '01'))
        self.assertFalse(check_answer('', ''))
        self.assertTrue(check_answer('−7', '-7'))

    def test_practice_answer_only_revealed_after_attempt(self):
        self.task.answer = 'PRIVATE_ANSWER_TOKEN'
        self.task.solution = 'PRIVATE_SOLUTION_TOKEN'
        self.task.save()
        self.assertNotContains(self.client.get(f'/tasks/{self.task.pk}/'), 'PRIVATE_ANSWER_TOKEN')
        response = self.client.post(f'/tasks/{self.task.pk}/', {'answer':'wrong'})
        self.assertContains(response, 'PRIVATE_SOLUTION_TOKEN')
        self.assertFalse(Practice.objects.get(user=self.student).correct)

    def test_bookmark_toggle(self):
        self.assertEqual(self.client.get(f'/tasks/{self.task.pk}/bookmark/').status_code,405)
        self.client.post(f'/tasks/{self.task.pk}/bookmark/')
        self.assertEqual(Bookmark.objects.filter(user=self.student).count(),1)
        self.client.post(f'/tasks/{self.task.pk}/bookmark/')
        self.assertEqual(Bookmark.objects.filter(user=self.student).count(),0)

    def test_mistake_cleared_after_correct_retry(self):
        self.client.post(f'/tasks/{self.task.pk}/', {'answer':'wrong'})
        self.assertContains(self.client.get('/catalog/?state=mistakes'), self.task.title)
        self.client.post(f'/tasks/{self.task.pk}/', {'answer':self.task.answer})
        self.assertNotContains(self.client.get('/catalog/?state=mistakes'), self.task.title)

    def test_generation_and_snapshot(self):
        v = create_variant(creator=self.teacher,title='Снимок',topics=[self.task.topic],count=3,published=True)
        self.assertEqual(v.items.count(),3)
        item=v.items.get(task=self.task)
        original=item.answer
        self.task.answer='CHANGED'
        self.task.save()
        item.refresh_from_db()
        self.assertEqual(item.answer,original)

    def test_generation_shortage_rolls_back(self):
        before=Variant.objects.count()
        with self.assertRaises(ValidationError):
            create_variant(creator=self.teacher,title='Недостаточно',topics=[self.task.topic],count=10)
        self.assertEqual(Variant.objects.count(),before)

    def test_manual_selection_order_and_missing_ids(self):
        ids=list(Task.objects.values_list('id',flat=True)[:3])[::-1]
        v=create_variant(creator=self.teacher,title='Вручную',topics=[],task_ids=','.join(map(str,ids)))
        self.assertEqual(list(v.items.values_list('task_id',flat=True)),ids)
        with self.assertRaises(ValidationError):
            create_variant(creator=self.teacher,title='Ошибка',topics=[],task_ids='999999')

    def test_generator_form_end_to_end(self):
        self.client.force_login(self.teacher)
        r=self.client.post('/teacher/variants/new/', {'title':'Новая КР','kind':'test','topics':[self.task.topic_id],'count':2,'difficulty':'easy','duration':30,'published':'on'})
        self.assertEqual(r.status_code,302)
        self.assertEqual(Variant.objects.get(title='Новая КР').items.count(),2)

    def test_private_group_and_drafts_are_inaccessible(self):
        group=StudyGroup.objects.create(title='Закрытая')
        group.students.add(self.other)
        self.variant.group=group
        self.variant.save()
        self.assertEqual(self.client.get(f'/variants/{self.variant.pk}/').status_code,404)
        self.assertEqual(self.client.post(f'/variants/{self.variant.pk}/start/').status_code,404)
        group.students.add(self.student)
        self.assertEqual(self.client.get(f'/variants/{self.variant.pk}/').status_code,200)
        self.variant.published=False
        self.variant.save()
        self.assertEqual(self.client.get(f'/variants/{self.variant.pk}/').status_code,404)

    def test_save_submit_and_immutable_results(self):
        attempt=self.start()
        self.client.post(f'/variants/{self.variant.pk}/start/')
        self.assertEqual(ExamAttempt.objects.filter(user=self.student).count(),1)
        answers={f'answer_{i.pk}':i.answer for i in self.variant.items.all()}
        self.client.post(f'/exam/{attempt.pk}/', {**answers,'action':'save'})
        attempt.refresh_from_db()
        self.assertIsNone(attempt.submitted_at)
        self.assertEqual(attempt.answers.count(),27)
        self.client.post(f'/exam/{attempt.pk}/', {**answers,'action':'submit'})
        attempt.refresh_from_db()
        self.assertEqual(attempt.score,27)
        self.assertContains(self.client.get(f'/results/{attempt.pk}/'), '100%')
        self.client.post(f'/exam/{attempt.pk}/', {'action':'submit'})
        attempt.refresh_from_db()
        self.assertEqual(attempt.score,27)

    def test_other_student_cannot_access_attempt_or_result(self):
        attempt=self.start()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/exam/{attempt.pk}/').status_code,404)
        self.assertEqual(self.client.get(f'/results/{attempt.pk}/').status_code,404)

    def test_expiry_uses_saved_answers_not_late_post(self):
        attempt=self.start()
        item=self.variant.items.first()
        ExamAnswer.objects.create(attempt=attempt,item=item,answer=item.answer)
        attempt.deadline=timezone.now()-timedelta(seconds=1)
        attempt.save()
        answers={f'answer_{i.pk}':i.answer for i in self.variant.items.all()}
        self.client.post(f'/exam/{attempt.pk}/', {**answers,'action':'submit'})
        attempt.refresh_from_db()
        self.assertEqual(attempt.score,1)
        self.assertIsNotNone(attempt.submitted_at)

    def test_quiz_marks_completion_and_cannot_forge_score(self):
        lesson=Lesson.objects.first()
        question=lesson.steps.get(kind='quiz')
        self.client.post(f'/learn/{question.pk}/', {'score':999, 'choice':'-1'})
        self.assertFalse(LessonProgress.objects.get(user=self.student,lesson=lesson).passed)
        self.client.post(f'/learn/{question.pk}/', {'choice':question.correct_choices})
        for step in lesson.steps.exclude(kind='quiz'):
            self.client.post(f'/learn/{step.pk}/', {'action':'complete'})
        self.assertTrue(LessonProgress.objects.get(user=self.student,lesson=lesson).passed)
        self.client.force_login(self.other)
        self.assertFalse(StepProgress.objects.filter(user=self.other,completed=True).exists())

    def test_quiz_validation(self):
        q=QuizQuestion(lesson=Lesson.objects.first(),text='test',options=['A'],correct_index=2)
        with self.assertRaises(ValidationError):
            q.full_clean()

    def test_upload_download_and_published_boundary(self):
        self.client.force_login(self.teacher)
        with tempfile.TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            response=self.client.post('/teacher/tasks/new/', {'topic':self.task.topic_id,'title':'Файл','statement':'Посчитайте','answer':'2','solution':'Разбор','difficulty':'easy','source':'Учитель','published':'on','attachment':SimpleUploadedFile('data.txt', b'1\n2\n')})
            self.assertEqual(response.status_code,302)
            task=Task.objects.get(title='Файл')
            block=task.content_blocks.get(kind='file')
            self.client.force_login(self.student)
            response=self.client.get(f'/files/block/{block.pk}/')
            self.assertEqual(b''.join(response.streaming_content), b'1\n2\n')
            self.assertIn('attachment', response['Content-Disposition'])
            task.published=False
            task.save()
            self.assertEqual(self.client.get(f'/files/block/{block.pk}/').status_code,404)

    def test_uploaded_html_rejected(self):
        self.client.force_login(self.teacher)
        response=self.client.post('/teacher/tasks/new/', {'topic':self.task.topic_id,'title':'Unsafe','statement':'Text','answer':'2','solution':'Text','difficulty':'easy','source':'Teacher','published':'on','attachment':SimpleUploadedFile('data.html', b'<script>alert(1)</script>')})
        self.assertEqual(response.status_code,200)
        self.assertFalse(Task.objects.filter(title='Unsafe').exists())

    def test_csrf_required(self):
        client=Client(enforce_csrf_checks=True)
        client.force_login(self.student)
        self.assertEqual(client.post(f'/tasks/{self.task.pk}/bookmark/').status_code,403)

    def test_seed_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            call_command('seed_demo',owner='teacher',verbosity=0)
        self.assertEqual(Topic.objects.count(),27)
        self.assertEqual(Task.objects.count(),81)
        self.assertEqual(Lesson.objects.count(),6)
        self.assertEqual(Variant.objects.count(),3)

    def test_csv_formula_injection(self):
        attempt=self.start()
        self.client.post(f'/exam/{attempt.pk}/', {'action':'submit'})
        self.variant.title='=1+2'
        self.variant.save()
        self.client.force_login(self.teacher)
        response=self.client.get('/teacher/?export=csv')
        self.assertContains(response, "'=1+2")

    def test_generated_examples_have_answers(self):
        from .management.commands.seed_demo import example
        for n in range(1,28):
            for k in range(1,4):
                statement,answer,solution,_=example(n,k)
                self.assertTrue(statement and answer and solution, (n,k))

    def test_video_range_support(self):
        lesson=Lesson.objects.first()
        with tempfile.TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            lesson.video.save('test.mp4',SimpleUploadedFile('test.mp4',b'0123456789'))
            response=self.client.get(f'/files/video/{lesson.pk}/', HTTP_RANGE='bytes=2-5')
            self.assertEqual(response.status_code,206)
            self.assertEqual(response['Content-Range'],'bytes 2-5/10')
            self.assertEqual(b''.join(response.streaming_content),b'2345')
            response=self.client.get(f'/files/video/{lesson.pk}/', HTTP_RANGE='bytes=-3')
            self.assertEqual(b''.join(response.streaming_content),b'789')
            self.assertEqual(self.client.get(f'/files/video/{lesson.pk}/',HTTP_RANGE='bytes=99-').status_code,416)

    def test_teacher_dashboard_finalizes_expired_work(self):
        attempt=self.start()
        attempt.deadline=timezone.now()-timedelta(seconds=1)
        attempt.save()
        self.client.force_login(self.teacher)
        self.client.get('/teacher/')
        attempt.refresh_from_db()
        self.assertIsNotNone(attempt.submitted_at)
