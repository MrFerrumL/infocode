"""Teacher-issued credentials are shown once and never saved in plaintext."""
import secrets
from django import forms
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.views import PasswordChangeView
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.views.decorators.debug import sensitive_variables
from .assignments import own_groups
from .models import AuditEvent, StudentAccess, StudentAssignment, StudyGroup
from .security import claim_submission, form_token
from .studio_views import teacher_students
from .views import staff_required


LATIN = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя', ['a', 'b', 'v', 'g', 'd', 'e', 'e', 'zh', 'z', 'i', 'y', 'k', 'l', 'm', 'n', 'o', 'p', 'r', 's', 't', 'u', 'f', 'kh', 'ts', 'ch', 'sh', 'shch', '', 'y', '', 'e', 'yu', 'ya']))
# Easy to dictate: no l/1/i or o/0 look-alikes.
PASSWORD_LETTERS, PASSWORD_DIGITS = 'abcdefghjkmnpqrstuvwxyz', '23456789'


def latin(text):
    return ''.join(LATIN.get(ch, ch if ch.isascii() and ch.isalnum() else '') for ch in text.lower())


def free_username(first_name, last_name):
    base = '.'.join(part for part in (latin(last_name), latin(first_name)) if part)[:140] or 'student'
    taken = {name.lower() for name in User.objects.filter(username__istartswith=base).values_list('username', flat=True)}
    if base not in taken:
        return base
    return next(f'{base}{n}' for n in range(2, 10_000) if f'{base}{n}' not in taken)


def simple_password():
    pick = secrets.choice
    return ''.join(pick(PASSWORD_LETTERS) for _ in range(4)) + ''.join(pick(PASSWORD_DIGITS) for _ in range(4))


class StudentAccountForm(forms.ModelForm):
    first_name = forms.CharField(label='Имя', max_length=150)
    last_name = forms.CharField(label='Фамилия', max_length=150)
    group = forms.ModelChoiceField(label='Сразу добавить в группу', queryset=StudyGroup.objects.none(), required=False)

    class Meta:
        model = User
        fields = ['first_name', 'last_name']

    def __init__(self, *args, teacher, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['group'].queryset = own_groups(teacher)


def claim_account_action(request, scope):
    if not request.POST.get('submit_token'):
        raise PermissionDenied('Обновите форму перед отправкой.')
    return claim_submission(request, scope)


@sensitive_variables('password')
def credentials_response(request, student, password):
    response = render(request, 'studio/student_credentials.html', {
        'student': student, 'temporary_password': password,
        'login_url': request.build_absolute_uri(reverse('login')), 'nav': 'teacher',
    })
    response['Referrer-Policy'] = 'no-referrer'
    return response


@staff_required
@never_cache
@require_http_methods(['GET', 'POST'])
@sensitive_variables('password')
def create_student(request):
    form = StudentAccountForm(request.POST or None, teacher=request.user)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                if not claim_account_action(request, 'student-account-create'):
                    messages.info(request, 'Аккаунт уже создан. Повторная отправка не создаёт нового ученика.')
                    return redirect('teacher_students')
                student = form.save(commit=False)
                student.username = free_username(student.first_name, student.last_name)
                password = simple_password()
                student.is_staff = student.is_superuser = False
                student.set_password(password)
                student.save()
                StudentAssignment.objects.create(teacher=request.user, student=student)
                # The teacher hands out these credentials, so they keep working until the pupil changes them.
                StudentAccess.objects.create(user=student, must_change_password=False)
                if form.cleaned_data['group']:
                    form.cleaned_data['group'].students.add(student)
                AuditEvent.objects.create(user=request.user, action='Выдача доступа', model='auth.user', object_id=student.pk, title=student.username)
            return credentials_response(request, student, password)
        except IntegrityError:
            form.add_error(None, 'Логин только что заняли. Отправьте форму ещё раз — платформа подберёт другой.')
    return render(request, 'studio/student_account.html', {
        'form': form, 'submit_token': form_token(request.user, 'student-account-create'), 'nav': 'teacher',
    })


@staff_required
@never_cache
@require_http_methods(['GET', 'POST'])
@sensitive_variables('password')
def reset_student_password(request, pk):
    students = User.objects.filter(is_staff=False, is_superuser=False)
    if not request.user.is_superuser:
        students = students.filter(pk__in=teacher_students(request.user).values('pk'))
    student = get_object_or_404(students, pk=pk)
    scope = f'student-password:{pk}'
    if request.method == 'POST':
        with transaction.atomic():
            if not claim_account_action(request, scope):
                return redirect('teacher_student', pk=pk)
            student = students.select_for_update().get(pk=pk)
            password = simple_password()
            student.set_password(password)
            student.save(update_fields=['password'])
            StudentAccess.objects.update_or_create(user=student, defaults={'must_change_password': False})
            AuditEvent.objects.create(user=request.user, action='Сброс пароля', model='auth.user', object_id=pk, title=student.username)
        return credentials_response(request, student, password)
    return render(request, 'studio/student_reset.html', {
        'student': student, 'submit_token': form_token(request.user, scope), 'nav': 'teacher',
    })


class DifferentPasswordForm(PasswordChangeForm):
    def clean(self):
        data = super().clean()
        password = data.get('new_password2')
        if password and self.user.check_password(password):
            self.add_error('new_password2', 'Новый пароль должен отличаться от прежнего.')
        return data


class StudentPasswordChangeView(PasswordChangeView):
    form_class = DifferentPasswordForm
    template_name = 'registration/password.html'
    success_url = '/'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['password_required'] = StudentAccess.objects.filter(user=self.request.user, must_change_password=True).exists()
        return context

    def form_valid(self, form):
        with transaction.atomic():
            response = super().form_valid(form)
            StudentAccess.objects.filter(user=self.request.user).update(must_change_password=False)
        messages.success(self.request, 'Пароль изменён. Теперь можно приступать к занятиям.')
        return response
