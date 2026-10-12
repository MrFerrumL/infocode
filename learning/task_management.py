"""Catalogue actions preserve issued variants, student answers and uploaded files."""
from urllib.parse import parse_qs, urlencode, urlsplit
from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from .models import Practice, Task, VariantItem
from .views import staff_required


def task_return_url(request):
    base = reverse('teacher_tasks')
    value = request.POST.get('return_to') or request.GET.get('next') or base
    try:
        parsed = urlsplit(value)
    except ValueError:
        return base
    if parsed.scheme or parsed.netloc or parsed.path != base:
        return base
    params = parse_qs(parsed.query)
    allowed = {key: values[-1][:200] for key, values in params.items()
               if key in {'q', 'topic', 'difficulty', 'status', 'page'}}
    return base + ('?' + urlencode(allowed) if allowed else '')


@staff_required
@require_http_methods(['GET', 'POST'])
@transaction.atomic
def delete_task(request, pk):
    from .platform_views import archive
    task = get_object_or_404(Task.all_objects.select_for_update(), pk=pk)
    return_to = task_return_url(request)
    if task.archived_at:
        messages.info(request, 'Задание уже находится в корзине.')
        return redirect(return_to)
    if request.method == 'POST' and request.POST.get('confirm') == 'yes':
        archive(request.user, task)
        messages.success(request, 'Задание удалено из каталога. Его можно восстановить в корзине; выданные КР и результаты сохранены.')
        return redirect(return_to)
    return render(request, 'studio/task_delete.html', {
        'tasks': [task], 'return_to': return_to, 'nav': 'teacher',
        'variants_count': VariantItem.objects.filter(task=task).values('variant').distinct().count(),
        'practices_count': Practice.objects.filter(task=task).count(),
    })
