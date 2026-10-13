from django import forms
from django.forms import modelformset_factory
from .models import Notebook, NotebookCell
from .content_editor import ContentBlockForm, ContentFormSet
from .assignments import own_groups


class NotebookForm(forms.ModelForm):
    class Meta:
        model = Notebook
        fields = ['title', 'description', 'group', 'published']
        widgets = {'description': forms.Textarea(attrs={'rows': 2, 'placeholder': 'О чём этот конспект'})}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['group'].queryset = own_groups(user)


class NotebookCellForm(ContentBlockForm):
    copy_from = forms.ModelChoiceField(queryset=NotebookCell.objects.none(), required=False, widget=forms.HiddenInput)

    class Meta(ContentBlockForm.Meta):
        model = NotebookCell
        fields = ['kind', 'position', 'text', 'caption', 'image', 'video', 'file']

    def __init__(self, *args, notebook=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['copy_from'].queryset = notebook.cells.all() if notebook and notebook.pk else NotebookCell.objects.none()


class NotebookCellFormSet(ContentFormSet):
    parent_field = 'notebook'


def notebook_formset(request, notebook):
    initial = [{'kind': 'text', 'position': 1}] if not notebook.pk else []
    factory = modelformset_factory(NotebookCell, form=NotebookCellForm, formset=NotebookCellFormSet,
                                  extra=len(initial), max_num=40, absolute_max=40, validate_max=True, can_delete=True)
    fs = factory(request.POST if request.method == 'POST' else None, request.FILES or None,
                 prefix='blocks', queryset=notebook.cells.all() if notebook.pk else NotebookCell.objects.none(),
                 initial=initial, form_kwargs={'user': request.user, 'notebook': notebook})
    for form in fs.forms:
        form.empty_permitted = False
    return fs
