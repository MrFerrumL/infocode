"""Upload a Colab notebook (.ipynb) into «Конспекты», and settings for notebooks too long for the cell editor."""
from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from .assignments import own_groups
from .ipynb_import import import_notebook
from .notebook_forms import NotebookForm
from .views import staff_required

EDITOR_LIMIT = 40  # the cell editor shows up to 40 cells


class ImportForm(forms.Form):
    file = forms.FileField(label='Файл из Colab (.ipynb)', help_text='Colab: Файл → Скачать → Скачать .ipynb. До 60 МБ.')
    title = forms.CharField(label='Название', max_length=180, required=False, help_text='Пусто — возьмём первый заголовок блокнота.')
    group = forms.ModelChoiceField(label='Доступ', queryset=None, required=False, empty_label='Все ученики')
    published = forms.BooleanField(label='Опубликовать для учеников', required=False, initial=True)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['group'].queryset = own_groups(user)

    def clean_file(self):
        upload = self.cleaned_data['file']
        if not upload.name.lower().endswith('.ipynb'):
            raise forms.ValidationError('Выберите файл с расширением .ipynb.')
        return upload


@staff_required
@require_http_methods(['GET', 'POST'])
def import_ipynb(request, pk=None):
    from .notebooks import editable_notebooks
    notebook = get_object_or_404(editable_notebooks(request.user), pk=pk) if pk else None
    initial = {'title': notebook.title, 'group': notebook.group, 'published': notebook.published} if notebook else {}
    form = ImportForm(request.POST or None, request.FILES or None, user=request.user, initial=initial)
    if request.method == 'POST' and form.is_valid():
        upload = form.cleaned_data['file']
        try:
            notebook, count = import_notebook(upload.read(), request.user, title=form.cleaned_data['title'], notebook=notebook,
                                              filename=upload.name, publish=form.cleaned_data['published'], group=form.cleaned_data['group'])
        except ValueError as exc:
            form.add_error('file', str(exc))
        else:
            messages.success(request, f'Конспект загружен из Colab: {count} ячеек.')
            return redirect('notebook_detail', pk=notebook.pk)
    return render(request, 'notebooks/import.html', {'form': form, 'notebook': notebook, 'nav': 'notebooks'})


def large_settings(request, notebook):
    """A notebook imported from Colab can be far longer than the cell editor allows: settings only, content via re-import."""
    form = NotebookForm(request.POST if request.method == 'POST' else None, instance=notebook, user=request.user)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Настройки конспекта сохранены.')
        return redirect('notebook_editor', pk=notebook.pk)
    return render(request, 'notebooks/large.html', {'form': form, 'notebook': notebook, 'count': notebook.cells.count(),
                                                    'limit': EDITOR_LIMIT, 'nav': 'notebooks'})
