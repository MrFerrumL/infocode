from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
from .middleware import active_actor
from .models import AuditEvent

TRACKED = {'Task', 'TaskBlock', 'StepContentBlock', 'Course', 'Section', 'Lesson', 'Step', 'StudyGroup', 'LearningAssignment', 'HomeworkSubmission'}


@receiver(pre_save)
def remember_before(sender, instance, **kwargs):
    if sender.__name__ not in TRACKED or not active_actor.get() or not instance.pk:
        return
    old = sender._base_manager.filter(pk=instance.pk).first()
    if old:
        instance._audit_before = {field.name: str(getattr(old, field.attname))[:2000] for field in sender._meta.concrete_fields if field.name not in ('id',)}


@receiver(post_save)
def record_change(sender, instance, created, **kwargs):
    actor = active_actor.get()
    if sender.__name__ not in TRACKED or not actor:
        return
    changes = {}
    for field in sender._meta.concrete_fields:
        if field.name == 'id':
            continue
        after = str(getattr(instance, field.attname))[:2000]
        before = getattr(instance, '_audit_before', {}).get(field.name, '')
        if created or before != after:
            changes[field.name] = {'before': before, 'after': after}
    if changes:
        AuditEvent.objects.create(user=actor, action='Создание' if created else 'Изменение', model=instance._meta.label_lower, object_id=instance.pk, title=str(instance)[:240], changes=changes)
    if actor.is_staff and sender.__name__ in {'Task', 'TaskBlock', 'Step', 'StepContentBlock', 'Course'}:
        from .models import MediaAsset
        for field in ('image', 'video', 'file', 'cover', 'attachment'):
            value = getattr(instance, field, None)
            if value and hasattr(value, 'name'):
                MediaAsset.objects.get_or_create(owner=actor, file=value.name, defaults={'title': (getattr(instance, 'title', '') or getattr(instance, 'text', '') or value.name.rsplit('/', 1)[-1])[:180], 'kind': 'image' if field in ('image', 'cover') else 'video' if field == 'video' else 'file'})


@receiver(post_delete)
def record_delete(sender, instance, **kwargs):
    actor = active_actor.get()
    if sender.__name__ in TRACKED and actor:
        AuditEvent.objects.create(user=actor, action='Удаление', model=instance._meta.label_lower, object_id=instance.pk, title=str(instance)[:240])
