"""«КР без регистрации» in the teacher workspace: build a КР from the bank, get its code, open its live page."""
import re
from urllib.parse import urljoin
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.dateparse import parse_datetime
from . import kr_client
from .kr_api import MAX_PER_NUMBER, MAX_VARIANTS, available
from .subjects import SUBJECTS, current as current_subject
from .views import staff_required

NUMBERS = {'inf': 27, 'phys': 26}


def _int(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def absolute(request, path):
    """A full address on the test platform; its paths already carry its prefix (/kr/...)."""
    public = kr_client.pupils_url()
    if path.startswith('http'):
        return path
    if public.startswith('http'):
        return urljoin(public, path)
    return request.build_absolute_uri(path)


@staff_required
def teacher_kr(request):
    subject = request.POST.get('subject') or request.GET.get('subject') or current_subject()
    subject = subject if subject in NUMBERS else 'inf'
    counts = available(subject)
    form = {'title': request.POST.get('title', 'Контрольная работа'), 'group': request.POST.get('group', ''),
            'variants': _int(request.POST.get('variants'), 4, 1, MAX_VARIANTS), 'duration': _int(request.POST.get('duration'), 45, 5, 300)}
    rows = [{'number': n, 'available': counts.get(n, 0), 'count': _int(request.POST.get(f'n{n}'), 0, 0, MAX_PER_NUMBER)}
            for n in range(1, NUMBERS[subject] + 1)]
    error = ''
    if request.method == 'POST' and kr_client.enabled():
        plan = [{'number': r['number'], 'count': r['count']} for r in rows if r['count']]
        if not plan:
            error = 'Укажите, сколько задач взять хотя бы по одному номеру.'
        else:
            try:
                made = kr_client.create(request.user, form['title'], form['group'], subject, plan, form['variants'], form['duration'])
            except kr_client.KrError as exc:
                error = str(exc)
            else:
                messages.success(request, f'КР «{made["test"]["title"]}» собрана и открыта. Код для учеников: {made["code"]}.')
                return redirect(f'{request.path}?subject={subject}&new={made["code"]}')
    tests, unavailable = [], ''
    if kr_client.enabled():
        try:
            tests = kr_client.tests_of(request.user)
        except kr_client.KrError as exc:
            unavailable = str(exc)
    for test in tests:
        test['subject_title'] = SUBJECTS.get(test['subject'], {}).get('title', test['subject'])
        test['created'] = parse_datetime(test['created_at'])
        for session in test['sessions']:
            session['created'] = parse_datetime(session['created_at'])
            session['link'] = absolute(request, session['join'])
    return render(request, 'teacher/kr.html', {
        'subject_code': subject, 'subject_choices': [(code, SUBJECTS[code]['title']) for code in NUMBERS],
        'form': form, 'rows': rows, 'error': error, 'tests': tests, 'unavailable': unavailable,
        'enabled': kr_client.enabled(), 'pupils_url': absolute(request, kr_client.pupils_url()), 'new_code': request.GET.get('new', ''),
        'max_per_number': MAX_PER_NUMBER, 'max_variants': MAX_VARIANTS, 'nav': 'teacher', 'workspace_section': 'works',
        'current_route': 'teacher_kr'})


@staff_required
def open_kr(request):
    """Sign the teacher in on the test platform and open the asked page there."""
    to = request.GET.get('to', '')
    if not re.fullmatch(r'(session:[A-Z0-9]{4,8}|test:\d{1,9})?', to):
        to = ''
    return redirect(kr_client.link(request.user, to))
