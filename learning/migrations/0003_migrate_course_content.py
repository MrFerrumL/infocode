from django.db import migrations

def migrate_content(apps, schema_editor):
    User=apps.get_model('auth','User')
    Course=apps.get_model('learning','Course')
    Section=apps.get_model('learning','Section')
    Lesson=apps.get_model('learning','Lesson')
    Step=apps.get_model('learning','Step')
    Assignment=apps.get_model('learning','StudentAssignment')
    owner=User.objects.filter(is_superuser=True).order_by('id').first()
    if owner:
        for student in User.objects.filter(is_staff=False):
            Assignment.objects.get_or_create(teacher=owner,student=student)
    if not Section.objects.filter(course=None).exists():
        return
    course=Course.objects.create(title='ЕГЭ по информатике · от основ к практике',description='Короткие шаги, практика и понятные объяснения.',owner=owner,published=True)
    Section.objects.filter(course=None).update(course=course)
    Quiz=apps.get_model('learning','QuizQuestion')
    File=apps.get_model('learning','LessonFile')
    OldProgress=apps.get_model('learning','LessonProgress')
    Progress=apps.get_model('learning','StepProgress')
    Enrollment=apps.get_model('learning','Enrollment')
    for lesson in Lesson.objects.all():
        position=0
        if lesson.video:
            position+=1
            Step.objects.create(lesson=lesson,position=position,title='Видеоурок',kind='video',video=str(lesson.video),points=0)
        if lesson.theory:
            position+=1
            Step.objects.create(lesson=lesson,position=position,title=lesson.title,kind='text',body=lesson.theory,points=0)
        for q in Quiz.objects.filter(lesson=lesson):
            position+=1
            Step.objects.create(lesson=lesson,position=position,title='Проверь себя',kind='quiz',body=q.text,options=q.options,correct_choices=[q.correct_index],explanation=q.explanation)
        for f in File.objects.filter(lesson=lesson):
            position+=1
            Step.objects.create(lesson=lesson,position=position,title=f.title,kind='file',file=str(f.file),points=0)
        for progress in OldProgress.objects.filter(lesson=lesson,passed=True):
            for step in Step.objects.filter(lesson=lesson):
                Progress.objects.get_or_create(user_id=progress.user_id,step=step,defaults={'completed':True,'points':step.points if step.kind=='quiz' else 0})
            Enrollment.objects.get_or_create(user_id=progress.user_id,course=course)

class Migration(migrations.Migration):
    dependencies=[('learning','0002_variantitem_blocks_alter_task_difficulty_and_more')]
    operations=[migrations.RunPython(migrate_content,migrations.RunPython.noop)]
