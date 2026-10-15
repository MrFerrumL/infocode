"""Store Python solutions as text. Submitted programs are never executed here."""
from django import forms

MAX_CODE_LENGTH = 20000
CODE_MODES = [('off', 'Без Python-кода'), ('optional', 'Код по желанию'), ('required', 'Код обязателен вместе с ответом')]
CODE_HELP = 'Ученик пишет программу вместе с ответом. Числовой/текстовый ответ проверяется автоматически, код сохраняется для просмотра преподавателем.'


def work_code_policy_field(initial=''):
    return forms.ChoiceField(label='Python-код в ответах на эту работу', required=False, initial=initial,
        choices=[('', 'Как настроено в каждой задаче'), ('optional', 'Разрешить код по желанию во всех задачах'),
                 ('required', 'Требовать код вместе с ответом во всех задачах'), ('off', 'Только ответы, без кода')],
        help_text='Для варианта / КР. Ученик может написать код или загрузить .py. Вы увидите код в результатах и получите уведомление о сдаче назначенной работы. Настройка действует только на эту работу.')


def code_field(mode='optional', required=None):
    return forms.CharField(
        label='Ваше решение на Python', max_length=MAX_CODE_LENGTH, strip=False,
        required=(mode == 'required') if required is None else required,
        help_text='До 20 000 символов. Отступы сохраняются. Код увидит преподаватель; программа не запускается.',
        widget=forms.Textarea(attrs={'rows': 10, 'class': 'python-answer-input', 'data-python-answer': '',
                                     'spellcheck': 'false', 'autocorrect': 'off', 'autocapitalize': 'off',
                                     'autocomplete': 'off', 'placeholder': '# Напишите или вставьте решение на Python\n'}),
    )


def normalize_code(value, required):
    if not value.strip():
        if required:
            raise forms.ValidationError('Приложите Python-код к ответу: это требуется в этом задании.')
        return ''
    return value


class PythonCodeForm(forms.Form):
    def __init__(self, *args, mode='off', required=None, **kwargs):
        super().__init__(*args, **kwargs)
        if mode != 'off':
            self.fields['python_code'] = code_field(mode, required)

    def clean(self):
        data = super().clean()
        field = self.fields.get('python_code')
        if field and 'python_code' in data:
            data['python_code'] = normalize_code(data['python_code'], field.required)
        return data


class ExamAnswerForm(PythonCodeForm):
    comment = forms.CharField(label='Комментарий к задаче', required=False, max_length=3000,
        help_text='Поясните решение или напишите, где возник вопрос. Комментарий увидит преподаватель.',
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': 'Как решали задачу, что получилось или осталось непонятным…'}))
