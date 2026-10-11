from django.db import migrations


def owners(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    Group = apps.get_model('learning', 'StudyGroup')
    Assignment = apps.get_model('learning', 'StudentAssignment')
    owner = User.objects.filter(is_superuser=True).order_by('pk').first()
    if owner:
        for group in Group.objects.filter(teacher=None):
            group.teacher_id = owner.pk
            group.save(update_fields=['teacher'])
            for student in group.students.filter(is_staff=False):
                Assignment.objects.get_or_create(teacher_id=owner.pk, student_id=student.pk)


class Migration(migrations.Migration):
    dependencies = [('learning', '0005_actionreceipt_assignmentrecipient_auditevent_and_more')]
    operations = [migrations.RunPython(owners, migrations.RunPython.noop)]
