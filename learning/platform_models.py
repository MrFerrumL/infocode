import secrets
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator, FileExtensionValidator
from .models import file_size


def invite_token():
    return secrets.token_urlsafe(32)


class LoginBucket(models.Model):
    key = models.CharField(max_length=64, unique=True)
    count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField()


class StudentAccess(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='student_access')
    must_change_password = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)


class ActionReceipt(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    scope = models.CharField(max_length=120)
    token = models.UUIDField(default=uuid.uuid4)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'scope', 'token'], name='unique_action_receipt')]


class TeacherNote(models.Model):
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='written_student_notes')
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='teacher_notes')
    text = models.TextField(max_length=5000)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at', '-id']


class LearningAssignment(models.Model):
    KINDS = [('homework', 'Домашнее задание'), ('required_test', 'Обязательная КР')]
    TARGETS = [('variant', 'Вариант / подборка заданий'), ('lesson', 'Урок курса'), ('manual', 'Решение с ручной проверкой')]
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='issued_assignments')
    title = models.CharField('Название', max_length=180)
    kind = models.CharField('Тип', max_length=20, choices=KINDS, default='homework')
    target = models.CharField('Что назначить', max_length=12, choices=TARGETS, default='variant')
    description = models.TextField('Инструкция', blank=True)
    variant = models.OneToOneField('learning.Variant', null=True, blank=True, on_delete=models.PROTECT, related_name='assignment')
    lesson = models.ForeignKey('learning.Lesson', null=True, blank=True, on_delete=models.PROTECT)
    group = models.ForeignKey('learning.StudyGroup', null=True, blank=True, on_delete=models.SET_NULL)
    starts_at = models.DateTimeField('Открыть с')
    due_at = models.DateTimeField('Сдать до')
    max_attempts = models.PositiveSmallIntegerField('Лимит попыток', default=1, validators=[MinValueValidator(0), MaxValueValidator(100)], help_text='0 — без ограничения для ДЗ. Для обязательной КР укажите от 1 до 100.')
    pass_percent = models.PositiveSmallIntegerField('Для зачёта, %', default=60, validators=[MaxValueValidator(100)])
    allow_late = models.BooleanField('Разрешить сдачу после срока', default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['teacher', 'due_at'])]
    def __str__(self):
        return self.title

    @property
    def unlimited_attempts(self):
        return self.kind == 'homework' and self.max_attempts == 0

    def clean(self):
        super().clean()
        if self.kind == 'required_test' and self.max_attempts == 0:
            raise ValidationError({'max_attempts': 'Для обязательной КР укажите хотя бы одну попытку.'})


class AssignmentRecipient(models.Model):
    assignment = models.ForeignKey(LearningAssignment, on_delete=models.CASCADE, related_name='recipients')
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='learning_assignments')
    assigned_at = models.DateTimeField(auto_now_add=True)
    extra_attempts = models.PositiveSmallIntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['assignment', 'student'], name='unique_assignment_student')]


class HomeworkSubmission(models.Model):
    recipient = models.ForeignKey(AssignmentRecipient, on_delete=models.CASCADE, related_name='submissions')
    text = models.TextField('Решение / комментарий', blank=True, max_length=20000)
    python_code = models.TextField('Python-код ученика', blank=True, max_length=20000)
    image = models.ImageField('Фото решения', upload_to='homework-images/', blank=True, validators=[file_size, FileExtensionValidator(['png', 'jpg', 'jpeg', 'webp'])])
    file = models.FileField('Файл решения', upload_to='homework-files/', blank=True, validators=[file_size, FileExtensionValidator(['txt', 'pdf', 'py', 'zip', 'csv', 'xlsx', 'docx'])])
    status = models.CharField(max_length=12, choices=[('pending', 'На проверке'), ('accepted', 'Зачтено'), ('returned', 'На доработку')], default='pending')
    score = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(100)])
    feedback = models.TextField('Комментарий преподавателя', blank=True, max_length=5000)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        ordering = ['-created_at', '-id']


class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=200)
    text = models.TextField(blank=True)
    url = models.CharField(max_length=400, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['user', 'read_at'])]


class GroupInvite(models.Model):
    group = models.ForeignKey('learning.StudyGroup', on_delete=models.CASCADE)
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    token = models.CharField(max_length=64, unique=True, default=invite_token)
    expires_at = models.DateTimeField()
    max_uses = models.PositiveIntegerField(default=100)
    uses = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


class InviteUse(models.Model):
    invite = models.ForeignKey(GroupInvite, on_delete=models.CASCADE)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['invite', 'student'], name='unique_invite_student')]


class EditorDraft(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    path = models.CharField(max_length=240)
    data = models.JSONField(default=dict)
    revision = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'path'], name='unique_editor_draft')]


class VideoPosition(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    step = models.ForeignKey('learning.Step', on_delete=models.CASCADE)
    seconds = models.PositiveIntegerField(default=0)
    block_positions = models.JSONField(default=dict, blank=True)
    revision = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'step'], name='unique_video_position')]


class MediaAsset(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    title = models.CharField('Название', max_length=180)
    kind = models.CharField('Тип', max_length=10, choices=[('image', 'Изображение'), ('video', 'Видео'), ('file', 'Файл')])
    file = models.FileField('Файл', upload_to='library/')
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self):
        return self.title


class AuditEvent(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=40)
    model = models.CharField(max_length=80)
    object_id = models.PositiveIntegerField(null=True)
    title = models.CharField(max_length=240, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at', '-id']


class TrashEntry(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    model = models.CharField(max_length=80)
    object_id = models.PositiveIntegerField()
    title = models.CharField(max_length=240)
    deleted_at = models.DateTimeField(auto_now_add=True)
    restored_at = models.DateTimeField(null=True, blank=True)


class ImportBatch(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    rows = models.JSONField(default=list)
    errors = models.JSONField(default=list)
    committed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
