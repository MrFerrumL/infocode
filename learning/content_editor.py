from django import forms
from django.forms import BaseModelFormSet, modelformset_factory
from .forms import AssetForm
from .models import StepContentBlock
from .rich_text import plain_html, sanitize_html


def legacy_content(step):
    rows = []
    if step.kind == 'video' and step.video:
        rows.append({'kind': 'video', 'video': step.video})
    if step.body:
        rows.append({'kind': 'text', 'text': plain_html(step.body)})
    if step.code:
        rows.append({'kind': 'code', 'text': step.code})
    if step.image:
        rows.append({'kind': 'image', 'image': step.image})
    if step.file:
        rows.append({'kind': 'file', 'file': step.file})
    return [dict(row, position=i) for i, row in enumerate(rows, 1)]


class ContentBlockForm(AssetForm):
    copy_from = forms.ModelChoiceField(queryset=StepContentBlock.objects.none(), required=False, widget=forms.HiddenInput)

    class Meta:
        model = StepContentBlock
        fields = ['kind', 'position', 'text', 'caption', 'image', 'video', 'file']
        widgets = {'text': forms.Textarea(attrs={'rows': 5, 'class': 'block-text-source', 'spellcheck': 'false'}),
                   'caption': forms.TextInput(attrs={'placeholder': 'Необязательная подпись'}),
                   'position': forms.NumberInput(attrs={'min': 1, 'max': 40})}

    def __init__(self, *args, step=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['copy_from'].queryset = StepContentBlock.objects.filter(step=step) if step and step.pk else StepContentBlock.objects.none()
        self.fields['text'].strip = False
        self.fields['text'].max_length = 100000
        self.fields['position'].max_value = 40
        self.fields['video'].widget.attrs['accept'] = 'video/mp4,video/webm'

    def clean(self):
        data = super().clean()
        kind = data.get('kind')
        source = data.get('copy_from')
        if source and source.kind == kind and kind in ('image', 'video', 'file') and not data.get(kind):
            data[kind] = getattr(source, kind)
        asset = data.get('asset')
        if asset and asset.kind != kind:
            self.add_error('asset', 'Тип материала должен совпадать с типом блока.')
        if kind == 'text':
            data['text'] = sanitize_html(data.get('text', ''))
        # Keep only the fields belonging to this kind; switching type cannot leak old media.
        for field in ('image', 'video', 'file'):
            if field != kind:
                data[field] = ''
        if kind not in ('text', 'code'):
            data['text'] = ''
        return data


class ContentFormSet(BaseModelFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        expected = set(self.get_queryset().values_list('pk', flat=True))
        ids = [f.cleaned_data['id'].pk for f in self.forms if f.cleaned_data.get('id')]
        if len(ids) != len(set(ids)) or set(ids) != expected or self.initial_form_count() != len(expected):
            raise forms.ValidationError('Состав блоков изменился. Обновите страницу; текст сохранён в черновике.')

    def active_forms(self):
        return sorted((f for f in self.forms if f.cleaned_data and not f.cleaned_data.get('DELETE')),
                      key=lambda f: f.cleaned_data.get('position', 1))

    def persist(self, step):
        for form in self.forms:
            if form.cleaned_data.get('DELETE') and form.instance.pk:
                form.instance.delete()
        for position, form in enumerate(self.active_forms(), 1):
            block = form.save(commit=False)
            block.step, block.position = step, position
            block.save()


def block_formset(request, step):
    initial = legacy_content(step) if not step.use_blocks else []
    if not step.pk and not initial:
        initial = [{'kind': request.GET.get('kind', 'text') if request.GET.get('kind') in ('video', 'file') else 'text', 'position': 1}]
    factory = modelformset_factory(StepContentBlock, form=ContentBlockForm, formset=ContentFormSet,
                                  extra=len(initial), max_num=40, validate_max=True,
                                  absolute_max=40, can_delete=True)
    fs = factory(request.POST if request.method == 'POST' else None, request.FILES or None,
                 prefix='blocks', queryset=step.content_blocks.all() if step.pk else StepContentBlock.objects.none(),
                 initial=initial, form_kwargs={'user': request.user, 'step': step})
    for form in fs.forms:
        form.empty_permitted = False
    return fs


def content_signature(rows):
    fields = ('kind', 'text', 'caption', 'image', 'video', 'file')
    return [tuple(str(row.get(field) or '') for field in fields) for row in rows]
