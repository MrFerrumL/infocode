from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import Topic, Task, StudyGroup, TaskBlock, Course, Section, Lesson, Step, StudentAssignment
from .python_answers import PythonCodeForm, work_code_policy_field


class ProtectedFileInput(forms.ClearableFileInput):
    template_name = 'widgets/protected_file.html'


class UploadModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field, forms.FileField):
                field.widget = ProtectedFileInput(attrs={'accept': 'image/png,image/jpeg,image/webp'} if isinstance(field, forms.ImageField) else {})


class RegisterForm(UserCreationForm):
    first_name = forms.CharField(label='Как вас зовут', max_length=150)
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ['first_name', 'username', 'password1', 'password2']


class AnswerForm(PythonCodeForm):
    answer = forms.CharField(label='Ваш ответ', max_length=250, widget=forms.TextInput(attrs={'placeholder': 'Введите ответ', 'autocomplete': 'off'}))


class VariantForm(forms.Form):
    code_policy = work_code_policy_field()
    title = forms.CharField(label='Название работы', max_length=180, initial='Тренировочный вариант')
    kind = forms.ChoiceField(label='Тип работы', choices=[('exam', 'Вариант'), ('test', 'Контрольная по темам')])
    topics = forms.ModelMultipleChoiceField(label='Номера заданий', queryset=Topic.objects.all(), required=False, widget=forms.CheckboxSelectMultiple)
    count = forms.IntegerField(label='Заданий каждого номера', min_value=1, max_value=3, initial=1)
    difficulty = forms.ChoiceField(label='Сложность', required=False, choices=[('', 'Любая сложность')] + Task.LEVELS)
    duration = forms.IntegerField(label='Время в минутах', min_value=1, max_value=600, initial=235)
    group = forms.ModelChoiceField(label='Назначить группе', queryset=StudyGroup.objects.all(), required=False, empty_label='Всем ученикам')
    task_ids = forms.CharField(label='Или выберите задания вручную', required=False, widget=forms.TextInput(attrs={'placeholder': 'ID через запятую: 1, 7, 15'}), help_text='Если заполнено, номера и фильтры выше не используются.')
    published = forms.BooleanField(label='Сразу опубликовать для учеников', required=False, initial=True)


class TaskForm(UploadModelForm):
    source = forms.CharField(label='Источник / автор', required=False, initial='Авторская учебная задача')
    statement_image = TaskBlock._meta.get_field('image').formfield(required=False, label='Фото условия', validators=TaskBlock._meta.get_field('image').validators)
    solution_image = TaskBlock._meta.get_field('image').formfield(required=False, label='Фото разбора', validators=TaskBlock._meta.get_field('image').validators)
    solution_file = TaskBlock._meta.get_field('file').formfield(required=False, label='Файл разбора (.py, PDF, таблица, ZIP)', validators=TaskBlock._meta.get_field('file').validators)
    solution_code = forms.CharField(label='Python-код разбора', required=False, strip=False, widget=forms.Textarea(attrs={'rows': 7, 'class': 'code-input', 'spellcheck': 'false', 'placeholder': 'for x in range(10):\n    print(x)'}))
    class Meta:
        model = Task
        fields = ['topic', 'title', 'difficulty', 'statement', 'answer', 'code_mode', 'solution', 'source', 'attachment', 'tags', 'subtopic', 'published']
        widgets = {'statement': forms.Textarea(attrs={'rows': 4}), 'solution': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['code_mode'].required = False

    def clean_code_mode(self):
        return self.cleaned_data.get('code_mode') or self.instance.code_mode or 'off'

    def clean_published(self):
        published = self.cleaned_data.get('published', False)
        if published and not (self.cleaned_data.get('statement') or self.cleaned_data.get('attachment') or self.files.get('statement_image') or (self.instance.pk and self.instance.content_blocks.filter(purpose='statement').exists())):
            raise forms.ValidationError('Сначала сохраните черновик и добавьте хотя бы один блок.')
        return published


class AssetForm(UploadModelForm):
    asset = forms.ModelChoiceField(label='Материал из медиатеки', queryset=None, required=False, help_text='Изображение, видео или файл. Выбранный материал заменит соответствующее вложение.')
    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        from .models import MediaAsset
        qs = MediaAsset.objects.all() if user and user.is_superuser else MediaAsset.objects.filter(owner=user) if user else MediaAsset.objects.none()
        if isinstance(self.instance, TaskBlock):
            qs = qs.exclude(kind='video')
        self.fields['asset'].queryset = qs
    def clean(self):
        data = super().clean()
        asset = data.get('asset')
        if asset:
            field = asset.kind if asset.kind in ('image', 'video') else 'file'
            if field in self.fields:
                setattr(self.instance, field, asset.file.name)
                data[field] = getattr(self.instance, field)
        return data


class TaskBlockForm(AssetForm):
    text = forms.CharField(label='Текст / подпись / код', required=False, strip=False, widget=forms.Textarea(attrs={'rows': 8}))
    class Meta:
        model = TaskBlock
        fields = ['kind', 'text', 'image', 'file']
        widgets = {'text': forms.Textarea(attrs={'rows': 4})}


class AssignmentForm(forms.Form):
    student = forms.ModelChoiceField(label='Ученик', queryset=User.objects.filter(is_staff=False, is_active=True), empty_label='Выберите зарегистрированного ученика')


class CourseForm(UploadModelForm):
    class Meta:
        model = Course
        fields = ['title', 'description', 'cover', 'group', 'sequential', 'published']
        widgets = {'description': forms.Textarea(attrs={'rows': 4})}


class SectionForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = ['title', 'description']
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}


class LessonForm(forms.ModelForm):
    # Accept older saved forms, but new lessons use a freely composed slide list.
    template = forms.ChoiceField(required=False, widget=forms.HiddenInput, choices=[('', ''), ('standard', 'standard'), ('practice', 'practice')])
    slide_plan = forms.CharField(required=False, max_length=30000, initial='[]', widget=forms.HiddenInput)

    def clean_slide_plan(self):
        import json
        from .lesson_composer import SLIDE_TYPES_BY_KEY, MAX_INITIAL_SLIDES
        raw = self.cleaned_data.get('slide_plan') or '[]'
        try:
            rows = json.loads(raw)
        except (ValueError, RecursionError):
            raise forms.ValidationError('Не удалось прочитать список слайдов. Соберите последовательность заново.')
        if not isinstance(rows, list) or len(rows) > MAX_INITIAL_SLIDES:
            raise forms.ValidationError('За один раз можно создать до 60 слайдов.')
        result = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('type'), str) or row['type'] not in SLIDE_TYPES_BY_KEY:
                raise forms.ValidationError('Выберите тип каждого слайда из списка.')
            title = row.get('title', '')
            if not isinstance(title, str) or len(title) > 180 or '\x00' in title:
                raise forms.ValidationError('Название слайда должно быть текстом до 180 символов.')
            result.append({'type': row['type'], 'title': title.strip()})
        return result

    class Meta:
        model = Lesson
        fields = ['title', 'minutes', 'published']


class StepForm(AssetForm):
    pairs_text = forms.CharField(label='Пропуски / пары: подсказка = ответ', required=False, widget=forms.Textarea(attrs={'rows': 6}), help_text='Каждая пара с новой строки. До 30 пар. Ответы не показываются ученику в пропусках; в сопоставлении они перемешиваются.')
    choices_text = forms.CharField(label='Варианты ответа — по одному на строке', required=False, widget=forms.Textarea(attrs={'rows': 5}), help_text='Для шага «Выбор ответа».')
    correct_numbers = forms.CharField(label='Номера верных ответов', required=False, help_text='Нумерация с 1. Например: 2 или 1, 3. Несколько номеров включают множественный выбор.')
    class Meta:
        model = Step
        fields = ['title', 'kind', 'body', 'code', 'quiz_mode', 'image', 'video', 'file', 'task', 'answer', 'code_mode', 'explanation', 'points', 'max_attempts', 'tolerance', 'pass_percent', 'published']
        widgets = {'task': forms.NumberInput(attrs={'min': 1, 'placeholder': 'Например, 616'}), 'body': forms.Textarea(attrs={'rows': 6}), 'code': forms.Textarea(attrs={'rows': 8, 'class': 'code-input', 'spellcheck': 'false'}), 'explanation': forms.Textarea(attrs={'rows': 4})}
    def __init__(self, *args, **kwargs):
        self.option_formset = kwargs.pop('option_formset', None)
        # Inactive assessment controls may still be in the browser draft. They
        # must neither block saving a different type nor become active answers.
        if args and args[0] is not None:
            data = args[0].copy()
            kind = data.get('kind')
            if kind in dict(Step.KINDS):
                if kind not in ('answer', 'numeric'):
                    data['answer'] = ''
                if kind != 'numeric':
                    data['tolerance'] = '0'
                if kind not in ('gaps', 'matching'):
                    data['pairs_text'] = ''
                    data['pass_percent'] = '100'
                if kind != 'quiz':
                    data.update({'quiz_mode': 'auto', 'choices_text': '', 'correct_numbers': ''})
                if kind != 'task':
                    data['task'] = ''
                if kind not in ('quiz', 'answer', 'numeric', 'gaps', 'matching', 'task'):
                    data.update({'points': '0', 'max_attempts': '0', 'code_mode': 'off', 'explanation': ''})
            args = (data, *args[1:])
        super().__init__(*args, **kwargs)
        self.fields['kind'].label = 'Задание на этом слайде'
        self.fields['kind'].choices = [
            ('text', 'Без задания — свободный материал'),
            ('video', 'Без задания — видео с материалами'),
            ('file', 'Без задания — файлы с материалами'),
            ('quiz', 'Тест с выбором ответов'),
            ('answer', 'Практика — короткий ответ'),
            ('numeric', 'Практика — числовой ответ'),
            ('gaps', 'Заполнение пропусков'),
            ('matching', 'Сопоставление пар'),
            ('task', 'Задача из банка — по ID из каталога'),
        ]
        self.fields['task'].label = 'ID задачи из банка'
        self.fields['task'].queryset = Task.objects.filter(published=True)
        self.fields['task'].error_messages['invalid_choice'] = 'Задача с таким ID не найдена среди опубликованных задач этого предмета.'
        self.fields['title'].label = 'Название слайда'
        self.fields['quiz_mode'].required = False
        self.fields['code_mode'].required = False
        self.fields['tolerance'].required = False
        self.fields['pass_percent'].required = False
        self.initial['pairs_text'] = '\n'.join(p['prompt']+' = '+p['answer'] for p in self.instance.pairs)
        self.fields['code'].strip = False
        if self.instance.pk:
            self.initial['choices_text'] = '\n'.join(self.instance.options)
            self.initial['correct_numbers'] = ', '.join(str(n+1) for n in self.instance.correct_choices)
    def clean(self):
        data = super().clean()
        data['quiz_mode'] = data.get('quiz_mode') or 'auto'
        data['code_mode'] = data.get('code_mode') or self.instance.code_mode or 'off'
        if data.get('kind') not in ('quiz', 'answer', 'numeric', 'gaps', 'matching', 'task'):
            data['code_mode'] = 'off'
        data['tolerance'] = data.get('tolerance') or 0
        data['pass_percent'] = data.get('pass_percent') or 100
        if data.get('kind') in ('gaps', 'matching'):
            pairs = []
            for line in data.get('pairs_text', '').splitlines():
                if not line.strip():
                    continue
                prompt, sep, answer = line.partition('=')
                if not sep or not prompt.strip() or not answer.strip():
                    self.add_error('pairs_text', 'Каждая строка: подсказка = ответ.')
                else:
                    pairs.append({'prompt': prompt.strip(), 'answer': answer.strip()})
            self.instance.pairs = pairs
        else:
            self.instance.pairs = []
        if self.option_formset is not None and data.get('kind') == 'quiz':
            self.instance.options = []
            self.instance.correct_choices = []
            if self.option_formset.is_valid():
                for row in self.option_formset.cleaned_data:
                    if row.get('text') and not row.get('DELETE'):
                        if row.get('correct'):
                            self.instance.correct_choices.append(len(self.instance.options))
                        self.instance.options.append(row['text'])
            return data
        self.instance.options = [s.strip() for s in data.get('choices_text', '').splitlines() if s.strip()]
        try:
            self.instance.correct_choices = sorted(set(int(n)-1 for n in data.get('correct_numbers', '').replace(',', ' ').split()))
        except ValueError:
            self.add_error('correct_numbers', 'Введите номера через запятую.')
            self.instance.correct_choices = []
        return data


class QuizOptionForm(forms.Form):
    text = forms.CharField(label='Вариант ответа', required=False, max_length=2000, widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'Напишите вариант ответа'}))
    correct = forms.BooleanField(label='Правильный', required=False)

    def clean(self):
        data = super().clean()
        if data.get('correct') and not data.get('text') and not data.get('DELETE'):
            self.add_error('text', 'Введите текст правильного ответа.')
        return data


QuizOptionFormSet = forms.formset_factory(QuizOptionForm, extra=2, min_num=2, max_num=50, validate_max=True, absolute_max=50, can_delete=True)
