"""Notebook permissions, editing and course integration. No Python execution."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods
from .models import Notebook, NotebookCell, Step, LessonProgress
from .views import staff_required
from .notebook_forms import NotebookForm, notebook_formset
from .content_editor import content_signature


def editable_notebooks(user):
    if not user.is_staff:
        return Notebook.objects.none()
    return Notebook.objects.all() if user.is_superuser else Notebook.objects.filter(owner=user)


def available_notebooks(user):
    public = Q(published=True) & (Q(group=None) | Q(group__students=user))
    if user.is_superuser:
        return Notebook.objects.all()
    return Notebook.objects.filter(public | Q(owner=user)).distinct()


def notebook_access(user, notebook, step_pk=None):
    if notebook.archived_at:
        raise Http404
    if step_pk is not None:
        from .course_views import step_access
        try:
            step_pk = int(step_pk)
        except (TypeError, ValueError):
            raise Http404
        if not 0 < step_pk < 2**63:
            raise Http404
        step = get_object_or_404(Step.objects.select_related('lesson__section__course'), pk=step_pk)
        step_access(user, step)
        if not step.use_blocks or not step.content_blocks.filter(kind='notebook', notebook=notebook).exists():
            raise Http404
        if not notebook.published and not (user.is_superuser or notebook.owner_id == user.pk):
            raise Http404
        return step
    if not available_notebooks(user).filter(pk=notebook.pk).exists():
        raise Http404
    return None


def invalidate_notebook_steps(notebook, unpublish=False):
    """Keep historical answers, while making completion specific to new content."""
    ids = list(Step.all_objects.filter(use_blocks=True, content_blocks__kind='notebook', content_blocks__notebook=notebook).values_list('pk', flat=True).distinct())
    steps = Step.all_objects.filter(pk__in=ids)
    lesson_ids = list(steps.values_list('lesson_id', flat=True).distinct())
    updates = {'revision': F('revision') + 1, 'edit_version': F('edit_version') + 1}
    if unpublish:
        updates['published'] = False
    steps.update(**updates)
    LessonProgress.objects.filter(lesson_id__in=lesson_ids).update(passed=False)
    return len(ids)


@login_required
@require_GET
def library(request):
    teacher = request.path.startswith('/teacher/')
    if teacher and not request.user.is_staff:
        raise PermissionDenied
    qs = editable_notebooks(request.user) if teacher else available_notebooks(request.user)
    query = request.GET.get('q', '').strip()[:200]
    status = request.GET.get('status', '') if teacher else ''
    if query:
        qs = qs.filter(Q(title__icontains=query) | Q(description__icontains=query))
    if status in ('published', 'draft'):
        qs = qs.filter(published=status == 'published')
    else:
        status = ''
    page = Paginator(qs.select_related('owner', 'group').annotate(cell_count=Count('cells', distinct=True)).order_by('-updated_at', '-id'), 18).get_page(request.GET.get('page'))
    return render(request, 'notebooks/library.html', {'page': page, 'query': query, 'status': status, 'teacher': teacher, 'nav': 'notebooks'})


@staff_required
@require_http_methods(['GET', 'POST'])
@transaction.atomic
def editor(request, pk=None):
    notebook = get_object_or_404(editable_notebooks(request.user).select_for_update(), pk=pk) if pk else Notebook(owner=request.user)
    from .notebook_import_views import EDITOR_LIMIT, large_settings
    if pk and notebook.cells.count() > EDITOR_LIMIT:
        return large_settings(request, notebook)
    version = notebook.edit_version
    original_title, original_published = notebook.title, notebook.published
    before = content_signature(list(notebook.cells.values())) if pk else []
    cells = notebook_formset(request, notebook)
    valid_cells = cells.is_valid() if request.method == 'POST' else True
    if request.method == 'POST':
        notebook._cell_count = len(cells.active_forms()) if valid_cells else 1
    form = NotebookForm(request.POST if request.method == 'POST' else None, instance=notebook, user=request.user)
    stale = bool(pk and request.method == 'POST' and request.POST.get('edit_version') != str(version))
    if stale:
        form.add_error(None, 'Конспект изменён в другой вкладке. Скопируйте свой текст и обновите страницу, чтобы не затереть изменения.')
    if request.method == 'POST' and not stale and form.is_valid() and valid_cells:
        notebook = form.save(commit=False)
        after = content_signature([f.cleaned_data for f in cells.active_forms()])
        changed = before != after or original_title != notebook.title or original_published != notebook.published or bool(request.FILES)
        if pk and changed:
            notebook.revision += 1
        notebook.edit_version += 1
        notebook.save()
        cells.persist(notebook)
        if pk and changed:
            count = invalidate_notebook_steps(notebook, unpublish=not notebook.published)
            if count and not notebook.published:
                messages.info(request, f'Связанных слайдов переведено в черновики: {count}. После публикации конспекта опубликуйте их заново.')
        messages.success(request, 'Конспект сохранён.')
        if request.POST.get('action') == 'preview':
            return redirect('notebook_detail', pk=notebook.pk)
        return redirect('notebook_editor', pk=notebook.pk)
    if request.method == 'POST' and request.FILES:
        messages.warning(request, 'Исправьте отмеченные поля и выберите новые файлы повторно перед сохранением.')
    linked = notebook.course_blocks.filter(kind='notebook', step__in=Step.objects.all()).select_related('step__lesson__section__course') if pk else []
    # Do not reveal other teachers’ private course names through a shared notebook.
    if pk and not request.user.is_superuser:
        linked = linked.filter(step__lesson__section__course__owner=request.user)
    return render(request, 'notebooks/editor.html', {'notebook': notebook, 'form': form, 'blocks': cells, 'linked': linked,
                  'editor_version': request.POST.get('edit_version', version) if stale else version, 'nav': 'notebooks'})


@login_required
@require_GET
def detail(request, pk):
    notebook = get_object_or_404(Notebook.objects.select_related('owner').prefetch_related('cells'), pk=pk)
    step = notebook_access(request.user, notebook, request.GET.get('step'))
    return render(request, 'notebooks/detail.html', {'notebook': notebook, 'step_context': step,
                  'can_edit_notebook': request.user.is_staff and (request.user.is_superuser or notebook.owner_id == request.user.pk), 'nav': 'notebooks'})


@staff_required
@require_http_methods(['GET', 'POST'])
@transaction.atomic
def delete(request, pk):
    notebook = get_object_or_404(editable_notebooks(request.user).select_for_update(), pk=pk)
    if request.method == 'POST':
        from .platform_views import archive
        archive(request.user, notebook)
        messages.success(request, 'Конспект перемещён в корзину. Связанные слайды переведены в черновики.')
        return redirect('teacher_notebooks')
    linked_count = Step.objects.filter(content_blocks__kind='notebook', content_blocks__notebook=notebook).distinct().count()
    return render(request, 'notebooks/delete.html', {'notebook': notebook, 'linked_count': linked_count, 'nav': 'notebooks'})
