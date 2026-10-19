"""«Пройденные номера»: a table like the teacher's sheet — groups in rows, «введение» and numbers 1–27 in columns.

The teacher ticks covered numbers and highlights cells yellow (на повторение) or red (требует внимания);
pupils see the same table for all groups of their teacher, read-only, with their own group highlighted.
"""
import re
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from .assignments import own_groups
from .coverage_models import GroupTopicMark
from .models import StudyGroup
from .subjects import info
from .views import staff_required

TOOLS = ('check', 'yellow', 'red', 'clear')


def columns():
    """«введение» and the numbers of the current subject (1–27 informatics, 1–26 physics)."""
    return [(0, 'введение')] + [(n, str(n)) for n in range(1, info()['numbers'] + 1)]


def short_title(group):
    return re.sub(r'^\s*Пара\s*\d+\s*:\s*', '', group.title) or group.title


def table(groups, mine=()):
    groups = sorted(groups, key=lambda g: g.title)
    marks = {(m.group_id, m.column): m for m in GroupTopicMark.objects.filter(group__in=groups)}
    rows = []
    for group in groups:
        cells = []
        for column, label in columns():
            mark = marks.get((group.pk, column))
            cells.append({'column': column, 'label': label, 'done': bool(mark and mark.done), 'color': mark.color if mark else ''})
        rows.append({'group': group, 'title': short_title(group), 'cells': cells, 'done': sum(c['done'] for c in cells), 'mine': group.pk in mine})
    totals = [sum(row['cells'][i]['done'] for row in rows) for i in range(len(columns()))]
    return rows, totals


@staff_required
def teacher_coverage(request):
    rows, totals = table(own_groups(request.user))
    return render(request, 'coverage.html', {'rows': rows, 'totals': totals, 'columns': columns(), 'editable': True,
                                             'size': len(columns()), 'nav': 'coverage'})


@staff_required
@require_POST
def mark(request):
    # A cell button posts "group:column"; the tool comes from the toolbar.
    group_id, _, column = request.POST.get('cell', '').partition(':')
    group = get_object_or_404(own_groups(request.user), pk=int(group_id) if group_id.isdigit() else 0)
    tool = request.POST.get('tool', 'check')
    if not column.isdigit() or not 0 <= int(column) <= info()['numbers'] or tool not in TOOLS:
        return JsonResponse({'error': 'Неверная ячейка.'}, status=400)
    item, _ = GroupTopicMark.objects.get_or_create(group=group, column=int(column))
    if tool == 'check':
        item.done = not item.done
    elif tool == 'clear':
        item.color = ''
    else:
        item.color = '' if item.color == tool else tool
    item.updated_by = request.user
    item.save()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        done = GroupTopicMark.objects.filter(group=group, done=True).count()
        return JsonResponse({'done': item.done, 'color': item.color, 'row_done': done})
    return redirect(f"{reverse('teacher_coverage')}?tool={tool}#group-{group.pk}")


@login_required
def coverage(request):
    """Pupils: all groups of their teacher(s), their own group first and highlighted."""
    mine = set(StudyGroup.objects.filter(students=request.user).values_list('pk', flat=True))
    teachers = Q(teacher__assigned_students__student=request.user) | Q(teacher__teaching_groups__students=request.user)
    groups = StudyGroup.objects.filter(teachers | Q(pk__in=mine)).distinct()
    rows, totals = table(groups, mine)
    rows.sort(key=lambda row: not row['mine'])
    return render(request, 'coverage.html', {'rows': rows, 'totals': totals, 'columns': columns(), 'editable': False,
                                             'size': len(columns()), 'nav': 'coverage'})
