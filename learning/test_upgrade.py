import io
import tempfile
from datetime import timedelta
from pathlib import Path
from PIL import Image
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.template.loader import get_template
from django.conf import settings
from django.utils import timezone
from .models import Task, Topic, TaskBlock, StudentAssignment, Course, Section, Lesson, Step, Enrollment, StepProgress, StepSubmission, StepComment, StudyGroup, Practice, ExamAttempt
from .services import create_variant
from .forms import StepForm


class UpgradeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin=User.objects.create_superuser('admin','','TestSecretPass!2026')
        cls.teacher=User.objects.create_user('teacher',is_staff=True)
        cls.stranger=User.objects.create_user('otherteacher',is_staff=True)
        cls.student=User.objects.create_user('learner',first_name='Ученик')
        cls.other=User.objects.create_user('classmate')
        cls.topic=Topic.objects.create(number=1,title='Графы')
        cls.task=Task.objects.create(topic=cls.topic,title='По картинке',answer='42',difficulty='ege')
        cls.course=Course.objects.create(title='Новый курс',owner=cls.teacher,published=True)
        cls.section=Section.objects.create(course=cls.course,title='Модуль 1')
        cls.lesson=Lesson.objects.create(section=cls.section,title='Урок 1',published=True)
        cls.text=Step.objects.create(lesson=cls.lesson,title='Теория',kind='text',body='Материал')
        cls.quiz=Step.objects.create(lesson=cls.lesson,title='Тест',kind='quiz',body='Выберите чётные',options=['1','2','4'],correct_choices=[1,2],explanation='SECRET_EXPLANATION',points=3,position=2,max_attempts=2)
        StudentAssignment.objects.create(teacher=cls.teacher,student=cls.student)
        Enrollment.objects.create(user=cls.student,course=cls.course)

    def setUp(self):
        self.client.force_login(self.student)
        self.tmp=tempfile.TemporaryDirectory()
        self.override=override_settings(MEDIA_ROOT=self.tmp.name)
        self.override.enable()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.override.disable)

    def image(self):
        stream=io.BytesIO()
        Image.new('RGB',(8,8),'white').save(stream,format='PNG')
        return SimpleUploadedFile('diagram.png',stream.getvalue(),content_type='image/png')

    def test_every_template_parses(self):
        for path in (settings.BASE_DIR/'templates').rglob('*.html'):
            with self.subTest(template=str(path)):
                get_template(str(path.relative_to(settings.BASE_DIR/'templates')).replace('\\','/'))

    def test_five_levels_render_in_filter_and_generator(self):
        r=self.client.get('/catalog/?difficulty=ege')
        for label in ['Простой','Средний','Сложный','ЕГЭ','Гроб']:
            self.assertContains(r,label)
        self.assertContains(r,self.task.title)
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get('/teacher/variants/new/'),'Гроб')

    def test_new_image_only_task_and_file_no_text(self):
        self.client.force_login(self.teacher)
        r=self.client.post('/teacher/tasks/new/',{'title':'Только фото','topic':self.topic.pk,'difficulty':'grave','answer':'42','statement':'','solution':''})
        self.assertEqual(r.status_code,302)
        task=Task.objects.get(title='Только фото')
        self.assertFalse(task.published)
        r=self.client.post(f'/teacher/tasks/{task.pk}/blocks/new/',{'kind':'image','image':self.image()})
        self.assertEqual(r.status_code,302)
        r=self.client.post(f'/teacher/tasks/{task.pk}/blocks/new/',{'kind':'file','file':SimpleUploadedFile('data.txt',b'42')})
        self.assertEqual(r.status_code,302)
        r=self.client.post(f'/teacher/tasks/{task.pk}/edit/',{'title':task.title,'topic':self.topic.pk,'difficulty':'grave','answer':'42','published':'on'})
        self.assertEqual(r.status_code,302)
        self.client.force_login(self.student)
        page=self.client.get(f'/tasks/{task.pk}/')
        self.assertContains(page,'<img ')
        self.assertContains(page,'Скачать файл к заданию')
        block=task.content_blocks.get(kind='image')
        response=self.client.get(f'/files/block/{block.pk}/')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response['Content-Type'],'image/png')
        self.assertIn('inline',response['Content-Disposition'])
        response.close()

    def test_empty_published_task_rejected(self):
        self.client.force_login(self.teacher)
        self.client.post('/teacher/tasks/new/',{'title':'Пусто','topic':self.topic.pk,'difficulty':'easy','answer':'1','published':'on'})
        self.assertFalse(Task.objects.filter(title='Пусто').exists())

    def test_bad_image_rejected(self):
        self.client.force_login(self.teacher)
        r=self.client.post(f'/teacher/tasks/{self.task.pk}/blocks/new/',{'kind':'image','image':SimpleUploadedFile('bad.png',b'<script>bad</script>',content_type='image/png')})
        self.assertEqual(r.status_code,200)
        self.assertFalse(self.task.content_blocks.exists())

    def test_deleting_last_block_unpublishes_empty_task(self):
        block=TaskBlock.objects.create(task=self.task,kind='text',text='Условие')
        self.task.published=True
        self.task.save()
        self.client.force_login(self.teacher)
        response=self.client.post(f'/teacher/blocks/{block.pk}/action/',{'action':'delete'})
        self.assertEqual(response.status_code,302)
        self.task.refresh_from_db()
        self.assertFalse(self.task.published)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(f'/tasks/{self.task.pk}/').status_code,404)

    def test_ordering_edits_and_snapshot_survive_block_deletion(self):
        a=TaskBlock.objects.create(task=self.task,kind='text',text='FIRST',position=1)
        b=TaskBlock.objects.create(task=self.task,kind='heading',text='SECOND',position=2)
        self.client.force_login(self.teacher)
        self.client.post(f'/teacher/blocks/{b.pk}/action/',{'action':'up'})
        self.assertEqual(list(self.task.content_blocks.values_list('pk',flat=True)),[b.pk,a.pk])
        v=create_variant(creator=self.teacher,title='Снимок',topics=[],task_ids=str(self.task.pk),published=True)
        item=v.items.get()
        self.assertEqual([b['text'] for b in item.blocks],['SECOND','FIRST'])
        self.client.post(f'/teacher/blocks/{b.pk}/action/',{'action':'delete'})
        a.text='CHANGED'
        a.save()
        self.assertContains(self.client.get(f'/teacher/variants/{v.pk}/print/'),'FIRST')
        self.assertContains(self.client.get(f'/teacher/variants/{v.pk}/print/'),'SECOND')
        self.assertNotContains(self.client.get(f'/teacher/variants/{v.pk}/print/'),'CHANGED')

    def test_snapshot_image_survives_replacement(self):
        block=TaskBlock.objects.create(task=self.task,kind='image',image=self.image())
        v=create_variant(creator=self.teacher,title='Фото',topics=[],task_ids=str(self.task.pk),published=True)
        item=v.items.get()
        original=Path(block.image.path).read_bytes()
        block.delete()
        response=self.client.get(f'/files/snapshot/{item.pk}/?block=0')
        self.assertEqual(b''.join(response.streaming_content),original)
        self.assertEqual(self.client.get(f'/files/snapshot/{item.pk}/?block=-1').status_code,404)

    def test_student_cannot_mutate_blocks_or_course(self):
        for path in [f'/teacher/tasks/{self.task.pk}/blocks/new/',f'/teacher/courses/{self.course.pk}/','/teacher/students/','/teacher/tasks/']:
            self.assertEqual(self.client.post(path,{}).status_code,302)

    def test_staff_only_own_students_and_results(self):
        Practice.objects.create(user=self.student,task=self.task,answer='42',correct=True)
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get('/teacher/students/'),'Ученик')
        self.assertEqual(self.client.get(f'/teacher/students/{self.student.pk}/').status_code,200)
        self.assertEqual(self.client.get(f'/teacher/students/{self.other.pk}/').status_code,404)
        self.assertEqual(self.client.post('/teacher/students/',{'student':self.other.pk}).status_code,403)
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(f'/teacher/students/{self.student.pk}/').status_code,404)

    def test_admin_can_attach_student(self):
        self.client.force_login(self.admin)
        self.client.post('/teacher/students/',{'student':self.other.pk})
        self.assertTrue(StudentAssignment.objects.filter(teacher=self.admin,student=self.other).exists())

    def test_course_editor_pages_and_ownership(self):
        self.client.force_login(self.teacher)
        paths=['/teacher/courses/',f'/teacher/courses/{self.course.pk}/',f'/teacher/courses/{self.course.pk}/report/',f'/teacher/sections/{self.section.pk}/lessons/{self.lesson.pk}/',f'/teacher/lessons/{self.lesson.pk}/steps/{self.quiz.pk}/',f'/learn/{self.quiz.pk}/',f'/teacher/tasks/{self.task.pk}/edit/']
        for path in paths:
            self.assertEqual(self.client.get(path).status_code,200,path)
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(f'/teacher/courses/{self.course.pk}/').status_code,404)
        self.assertEqual(self.client.get(f'/teacher/lessons/{self.lesson.pk}/steps/{self.quiz.pk}/').status_code,404)
        self.assertEqual(self.client.post(f'/teacher/structure/step/{self.quiz.pk}/',{'action':'up'}).status_code,403)

    def test_enrollment_and_private_course_permissions(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/learn/{self.text.pk}/').status_code,403)
        self.client.post(f'/course/{self.course.pk}/enroll/')
        self.assertEqual(self.client.get(f'/learn/{self.text.pk}/').status_code,200)
        group=StudyGroup.objects.create(title='Закрытая')
        group.students.add(self.student)
        self.course.group=group
        self.course.save()
        self.assertEqual(self.client.get(f'/learn/{self.text.pk}/').status_code,404)
        self.assertEqual(self.client.get(f'/course/{self.course.pk}/').status_code,404)

    def test_multi_choice_exact_match_and_attempt_limit(self):
        self.assertNotContains(self.client.get(f'/learn/{self.quiz.pk}/'),'SECRET_EXPLANATION')
        self.client.post(f'/learn/{self.quiz.pk}/',{'choice':['1']})
        p=StepProgress.objects.get(user=self.student,step=self.quiz)
        self.assertFalse(p.completed)
        self.assertEqual(p.attempts,1)
        self.client.post(f'/learn/{self.quiz.pk}/',{'choice':['1','2']})
        p.refresh_from_db()
        self.assertTrue(p.completed)
        self.assertEqual(p.points,3)
        self.client.post(f'/learn/{self.quiz.pk}/',{'choice':['0']})
        p.refresh_from_db()
        self.assertEqual(p.attempts,2)
        self.assertEqual(p.points,3)
        self.assertContains(self.client.get(f'/learn/{self.quiz.pk}/'),'SECRET_EXPLANATION')

    def test_cannot_complete_quiz_by_forged_complete(self):
        r=self.client.post(f'/learn/{self.quiz.pk}/',{'action':'complete','points':999})
        self.assertEqual(r.status_code,403)
        self.assertFalse(StepProgress.objects.filter(user=self.student,step=self.quiz,completed=True).exists())

    def test_post_redirect_prevents_refresh_duplicate_attempt(self):
        r=self.client.post(f'/learn/{self.quiz.pk}/',{'choice':['0']})
        self.assertEqual(r.status_code,302)
        self.client.get(r.url)
        self.client.get(r.url)
        self.assertEqual(StepSubmission.objects.filter(user=self.student,step=self.quiz).count(),1)

    def test_text_completion_and_preview_does_not_write_progress(self):
        self.client.post(f'/learn/{self.text.pk}/',{'action':'complete'})
        self.assertTrue(StepProgress.objects.get(user=self.student,step=self.text).completed)
        self.client.force_login(self.teacher)
        self.client.post(f'/learn/{self.quiz.pk}/',{'choice':['1','2']})
        self.assertFalse(StepProgress.objects.filter(user=self.teacher).exists())

    def test_short_answer_step(self):
        s=Step.objects.create(lesson=self.lesson,title='Ответ',kind='answer',answer='01 7',points=2)
        self.client.post(f'/learn/{s.pk}/',{'answer':'1 7'})
        self.assertFalse(StepProgress.objects.get(user=self.student,step=s).completed)
        self.client.post(f'/learn/{s.pk}/',{'answer':' 01   7 '})
        self.assertEqual(StepProgress.objects.get(user=self.student,step=s).points,2)

    def step_data(self):
        return {'title':self.quiz.title,'kind':'quiz','body':self.quiz.body,'choices_text':'1\n2\n4','correct_numbers':'2, 3','points':3,'max_attempts':2,'published':'on'}

    def test_step_editor_creates_quiz_from_human_options(self):
        self.client.force_login(self.teacher)
        r=self.client.post(f'/teacher/lessons/{self.lesson.pk}/steps/new/',self.step_data())
        self.assertEqual(r.status_code,302)
        step=Step.objects.filter(lesson=self.lesson).order_by('-pk').first()
        self.assertEqual(step.options,['1','2','4'])
        self.assertEqual(step.correct_choices,[1,2])

    def test_content_edit_invalidates_old_progress_but_keeps_history(self):
        self.client.post(f'/learn/{self.quiz.pk}/',{'choice':['1','2']})
        self.client.force_login(self.teacher)
        data=self.step_data()
        data['body']='Изменённое условие'
        r=self.client.post(f'/teacher/lessons/{self.lesson.pk}/steps/{self.quiz.pk}/',data)
        self.assertEqual(r.status_code,302)
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.revision,2)
        self.client.force_login(self.student)
        r=self.client.get(f'/learn/{self.quiz.pk}/')
        self.assertNotContains(r,'✓ Шаг пройден')
        self.client.post(f'/learn/{self.quiz.pk}/',{'choice':['0']})
        self.assertEqual(StepProgress.objects.get(user=self.student,step=self.quiz).attempts,1)
        self.assertEqual(StepSubmission.objects.filter(user=self.student,step=self.quiz).count(),2)

    def test_invalid_correct_option_rejected(self):
        self.client.force_login(self.teacher)
        data=self.step_data()
        data['correct_numbers']='99'
        before=Step.objects.count()
        r=self.client.post(f'/teacher/lessons/{self.lesson.pk}/steps/new/',data)
        self.assertEqual(r.status_code,200)
        self.assertEqual(Step.objects.count(),before)

    def test_drafts_and_their_files_are_private(self):
        self.text.image.save('diagram.png',self.image())
        self.text.published=False
        self.text.save()
        self.assertEqual(self.client.get(f'/learn/{self.text.pk}/').status_code,404)
        self.assertEqual(self.client.get(f'/files/step-image/{self.text.pk}/').status_code,404)
        self.assertNotContains(self.client.get(f'/course/{self.course.pk}/'),f'/learn/{self.text.pk}/')

    def test_comments_escape_html_and_moderation(self):
        self.client.post(f'/learn/{self.text.pk}/',{'action':'comment','comment':'<script>alert(1)</script>'})
        comment=StepComment.objects.get(step=self.text)
        page=self.client.get(f'/learn/{self.text.pk}/')
        self.assertContains(page,'&lt;script&gt;')
        self.assertNotContains(page,'<script>alert(1)</script>')
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(f'/comments/{comment.pk}/hide/').status_code,403)
        self.client.force_login(self.teacher)
        self.client.post(f'/comments/{comment.pk}/hide/')
        comment.refresh_from_db()
        self.assertTrue(comment.hidden)
