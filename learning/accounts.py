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


class StudentAccountForm(forms.ModelForm):
    first_name = forms.CharField(label='Имя ученика', max_length=150)
    group = forms.ModelChoiceField(label='Сразу добавить в группу', queryset=StudyGroup.objects.none(), required=False)

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'username']
        labels = {'last_name': 'Фамилия', 'username': 'Логин для входа'}
        help_texts = {'username': 'Например: ivan.petrov. Временный пароль создаст платформа.'}

    def __init__(self, *args, teacher, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['group'].queryset = own_groups(teacher)

    def clean_username(self):
        name = self.cleaned_data['username']
        if User.objects.filter(username__iexact=name).exists():
            raise forms.ValidationError('Такой логин уже занят. Придумайте другой или используйте приглашение для существующего аккаунта.')
        return name


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
                password = secrets.token_urlsafe(18)
                student.is_staff = student.is_superuser = False
                student.set_password(password)
                student.save()
                StudentAssignment.objects.create(teacher=request.user, student=student)
                StudentAccess.objects.create(user=student)
                if form.cleaned_data['group']:
                    form.cleaned_data['group'].students.add(student)
                AuditEvent.objects.create(user=request.user, action='Выдача доступа', model='auth.user', object_id=student.pk, title=student.username)
            return credentials_response(request, student, password)
        except IntegrityError:
            form.add_error('username', 'Логин уже занят. Выберите другой.')
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
            password = secrets.token_urlsafe(18)
            student.set_password(password)
            student.save(update_fields=['password'])
            StudentAccess.objects.update_or_create(user=student, defaults={'must_change_password': True})
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
