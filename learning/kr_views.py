"""«КР без регистрации» in the teacher workspace: build a КР from the bank, get its code, open its live page."""
import random
import re
from urllib.parse import urljoin
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.utils.dateparse import parse_datetime
from . import kr_client, ocr, semantic_search
from .kr_api import LEVELS, MAX_PER_NUMBER, MAX_VARIANTS, MAX_PICKED, SOURCES, ComposeError, available, by_source, from_bank, pool
from .models import Task
from .exam_sources import OFFICIAL, apply as apply_source
from .similar_tasks import by_vector
from .subjects import SUBJECTS, current as current_subject, using
from .task_generators import REGISTRY
from .views import staff_required

NUMBERS = {'inf': 27, 'phys': 26}
SOURCE_CHOICES = {'official': 'Все официальные (ЕГЭ)', **dict(Task.EXAM_SOURCES)}


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
    # A link from the test platform («Собрать КР по слабым номерам») fills the form through the address.
    data = request.POST if request.method == 'POST' else request.GET
    form = {'title': data.get('title', 'Контрольная работа')[:160], 'group': data.get('group', '')[:120],
            'variants': _int(data.get('variants'), 4, 1, MAX_VARIANTS), 'duration': _int(data.get('duration'), 45, 5, 300),
            'level': data.get('level') if data.get('level') in LEVELS else '',
            'source': data.get('source') if data.get('source') in SOURCE_CHOICES else ''}
    generators = set(REGISTRY) if subject == 'inf' else set()
    picked = [int(i) for i in request.POST.get('picked', '').split(',') if i.isdigit()][:MAX_PICKED]
    with using(subject):
        picked_numbers = dict(pool().filter(pk__in=picked).values_list('pk', 'topic__number'))
        # The bank by number, level and exam source: the page recounts «в банке N» when the level or source changes.
        bank = [list(row) for row in pool().order_by().values('topic__number', 'difficulty', 'exam_source')
                .annotate(k=Count('pk')).values_list('topic__number', 'difficulty', 'exam_source', 'k')]
    rows = []
    for n in range(1, NUMBERS[subject] + 1):
        source = request.POST.get(f'src{n}', 'bank')
        source = source if source in ('bank', 'pick') or (source == 'generate' and n in generators) else 'bank'
        rows.append({'number': n, 'available': counts.get(n, 0), 'count': _int(data.get(f'n{n}'), 0, 0, MAX_PER_NUMBER),
                     'source': source, 'generator': n in generators, 'ids': [i for i in picked if picked_numbers.get(i) == n]})
    error = ''
    if request.method == 'POST' and kr_client.enabled():
        plan = [r for r in rows if r['count']]
        if not plan:
            error = 'Укажите, сколько задач взять хотя бы по одному номеру.'
        else:
            try:
                variants = compose_draft(subject, plan, form['variants'], form['level'], form['source'])
            except ComposeError as exc:
                error = str(exc)
            else:
                request.session[DRAFT] = {'subject': subject, 'title': form['title'], 'group': form['group'], 'duration': form['duration'],
                                          'level': form['level'], 'source': form['source'], 'variants': variants}
                return redirect('teacher_kr_draft')
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
        'max_per_number': MAX_PER_NUMBER, 'max_variants': MAX_VARIANTS, 'picked': ','.join(map(str, picked)),
        'bank': {'rows': bank, 'official': sorted(OFFICIAL)},
        'levels': LEVELS.items(), 'has_draft': bool(request.session.get(DRAFT)), 'source_choices': SOURCE_CHOICES.items(),
        'meaning_on': semantic_search.available(), 'photo_on': ocr.available(),
        'nav': 'teacher', 'workspace_section': 'works',
        'current_route': 'teacher_kr'})


@staff_required
def open_kr(request):
    """Sign the teacher in on the test platform and open the asked page there."""
    to = request.GET.get('to', '')
    if not re.fullmatch(r'(session:[A-Z0-9]{4,8}|results:[A-Z0-9]{4,8}|test:\d{1,9}|pupils)?', to):
        to = ''
    return redirect(kr_client.link(request.user, to))


@staff_required
def kr_tasks(request):
    """Catalogue tasks for the КР picker: by number, ID or words of the condition."""
    subject = request.GET.get('subject') if request.GET.get('subject') in NUMBERS else 'inf'
    number, query = request.GET.get('number', ''), request.GET.get('q', '').strip()[:1500]
    level, source = request.GET.get('level', ''), request.GET.get('source', '')
    meaning = request.GET.get('mode') == 'meaning' and semantic_search.available()
    with using(subject):
        tasks = pool().select_related('topic').order_by('-pk')
        if number.isdigit():
            tasks = tasks.filter(topic__number=int(number))
        if level in LEVELS:
            tasks = tasks.filter(difficulty=level)
        if source in SOURCE_CHOICES:
            tasks = by_source(tasks, source)
        ranked = semantic_search.rank(query, tasks, limit=40) if query and meaning and not query.isdigit() else None
        if ranked is not None:
            # By meaning (words or a photo's text): the closest tasks first.
            order = Case(*[When(pk=pk, then=Value(i)) for i, pk in enumerate(ranked)], default=Value(len(ranked)), output_field=IntegerField())
            tasks = tasks.filter(pk__in=ranked).order_by(order)
        elif query.isdigit():
            tasks = tasks.filter(Q(pk=int(query)) | Q(external_id=int(query)))
        elif query:
            tasks = tasks.filter(Q(statement__icontains=query[:100]) | Q(title__icontains=query[:100]) | Q(subtopic__icontains=query[:100]))
        page = Paginator(tasks, 8).get_page(request.GET.get('page'))
        cards = list(page.object_list.prefetch_related('content_blocks'))
    return render(request, 'teacher/kr_tasks.html', {'page': page, 'cards': cards})


DRAFT = 'kr_draft'


def compose_draft(subject, plan, variants, level, source=''):
    """Variants as lists of task ids, ordered by ЕГЭ number; the same sources as the КР API."""
    rng = random.SystemRandom()
    result = [[] for _ in range(variants)]
    with using(subject):
        for row in plan:
            if row['source'] == 'bank':
                chosen = from_bank(row['number'], row['count'], variants, row['ids'], rng, level or None, source=source)
            else:
                chosen = SOURCES[row['source']](row['number'], row['count'], variants, row['ids'], rng, level or None)
            for v in range(variants):
                result[v].extend(chosen[v * row['count']:(v + 1) * row['count']])
        numbers = dict(Task.all_objects.filter(pk__in={i for ids in result for i in ids}).values_list('pk', 'topic__number'))
    return [sorted(ids, key=lambda i: (numbers.get(i, 0), ids.index(i))) for ids in result]


def _other(draft, number, exclude_level=False):
    """One more random task of the number that is not in the draft yet."""
    used = {i for ids in draft['variants'] for i in ids}
    level = None if exclude_level else draft.get('level') or None
    source = draft.get('source', '')
    with using(draft['subject']):
        try:
            return from_bank(number, 1, 1, [], random.SystemRandom(), level, exclude=used, source=source)[0]
        except ComposeError:
            if level or source:
                return from_bank(number, 1, 1, [], random.SystemRandom(), None, exclude=used)[0]
            raise


def _place(draft, v, task_id, at=None):
    ids = draft['variants'][v]
    if at is None:
        ids.append(task_id)
    else:
        ids[at] = task_id
    with using(draft['subject']):
        numbers = dict(Task.all_objects.filter(pk__in=ids).values_list('pk', 'topic__number'))
    draft['variants'][v] = sorted(ids, key=lambda i: (numbers.get(i, 0), ids.index(i)))


@staff_required
def teacher_kr_draft(request):
    """The assembled variants before they go to the test platform: remove, swap, pick or add tasks."""
    draft = request.session.get(DRAFT)
    if not draft:
        messages.info(request, 'Черновика КР нет — соберите его заново.')
        return redirect('teacher_kr')
    if request.method == 'POST':
        action = request.POST.get('action', '')
        v, at = _int(request.POST.get('v'), -1, -1, len(draft['variants']) - 1), _int(request.POST.get('i'), -1, -1, 99)
        number = _int(request.POST.get('number'), 0, 0, NUMBERS[draft['subject']])
        task_id = _int(request.POST.get('task'), 0, 0, 2_000_000_000)
        try:
            if action == 'remove' and v >= 0 and 0 <= at < len(draft['variants'][v]):
                draft['variants'][v].pop(at)
            elif action == 'swap' and v >= 0 and 0 <= at < len(draft['variants'][v]):
                with using(draft['subject']):
                    current = Task.all_objects.filter(pk=draft['variants'][v][at]).values_list('topic__number', flat=True).first()
                _place(draft, v, _other(draft, current), at)
            elif action == 'like' and v >= 0 and 0 <= at < len(draft['variants'][v]):
                # The closest task by meaning of the same number that is not in the draft yet.
                used = {i for ids in draft['variants'] for i in ids}
                with using(draft['subject']):
                    current = Task.all_objects.select_related('topic').get(pk=draft['variants'][v][at])
                    found = by_vector(current, pool().exclude(pk__in=used), [current.topic.number], 1)
                if not found:
                    raise ComposeError('Для этой задачи нет похожих по смыслу — попробуйте «↻ Другая» или «🔍 Выбрать».')
                _place(draft, v, found[0], at)
            elif action in ('put', 'add') and v >= 0 and task_id:
                with using(draft['subject']):
                    if not pool().filter(pk=task_id).exists():
                        raise ComposeError('Эту задачу нельзя взять в КР: у неё нет ответа или она скрыта.')
                if task_id in draft['variants'][v]:
                    raise ComposeError('Эта задача уже есть в варианте.')
                _place(draft, v, task_id, at if action == 'put' and 0 <= at < len(draft['variants'][v]) else None)
            elif action == 'add_random' and v >= 0 and number:
                _place(draft, v, _other(draft, number))
            elif action == 'add_number' and number:
                for k in range(len(draft['variants'])):
                    _place(draft, k, _other(draft, number))
            elif action == 'remove_number' and number:
                with using(draft['subject']):
                    drop = set(Task.all_objects.filter(topic__number=number, pk__in={i for ids in draft['variants'] for i in ids})
                               .values_list('pk', flat=True))
                draft['variants'] = [[i for i in ids if i not in drop] for ids in draft['variants']]
            elif action == 'discard':
                del request.session[DRAFT]
                return redirect('teacher_kr')
            elif action == 'send':
                if not all(draft['variants']):
                    raise ComposeError('В каждом варианте должна быть хотя бы одна задача.')
                made = kr_client.create(request.user, draft['title'], draft['group'], draft['subject'], [], len(draft['variants']),
                                        draft['duration'], exact=draft['variants'])
                del request.session[DRAFT]
                messages.success(request, f'КР «{made["test"]["title"]}» отправлена и открыта. Код для учеников: {made["code"]}.')
                return redirect(f'{reverse("teacher_kr")}?subject={draft["subject"]}&new={made["code"]}')
        except (ComposeError, kr_client.KrError) as exc:
            messages.error(request, str(exc))
        request.session[DRAFT] = draft
        return redirect(f'{reverse("teacher_kr_draft")}#v{max(v, 0) + 1}')
    with using(draft['subject']):
        tasks = {t.pk: t for t in Task.all_objects.filter(pk__in={i for ids in draft['variants'] for i in ids})
                 .select_related('topic').prefetch_related('content_blocks')}
    variants = [{'number': k + 1, 'index': k, 'tasks': [tasks[i] for i in ids if i in tasks]} for k, ids in enumerate(draft['variants'])]
    numbers = sorted({t.topic.number for t in tasks.values()})
    return render(request, 'teacher/kr_draft.html', {
        'draft': draft, 'variants': variants, 'numbers': numbers, 'all_numbers': range(1, NUMBERS[draft['subject']] + 1),
        'level_title': LEVELS.get(draft.get('level'), 'любой'), 'nav': 'teacher', 'workspace_section': 'works',
        'levels': LEVELS.items(), 'source_choices': SOURCE_CHOICES.items(),
        'meaning_on': semantic_search.available(), 'photo_on': ocr.available(),
        'current_route': 'teacher_kr', 'picker_url': f'{reverse("teacher_kr_tasks")}?subject={draft["subject"]}'})


@staff_required
@require_POST
def again_kr(request):
    """The same КР once more with a new code (another group, another day)."""
    test_id = _int(request.POST.get('test'), 0, 0, 2_000_000_000)
    try:
        made = kr_client.open_again(request.user, test_id)
    except kr_client.KrError as exc:
        messages.error(request, str(exc))
        return redirect('teacher_kr')
    messages.success(request, f'КР «{made["test"]["title"]}» открыта ещё раз. Новый код для учеников: {made["code"]}.')
    return redirect(f'{reverse("teacher_kr")}?new={made["code"]}')


@staff_required
@require_POST
def classify_task(request, pk):
    """The teacher moves a task to another level or exam source at once; the automatic sorting leaves it alone after."""
    from django.http import JsonResponse
    task = Task.all_objects.filter(pk=pk).first()
    if not task:
        return JsonResponse({'error': 'Нет такой задачи.'}, status=404)
    sources = dict(Task._meta.get_field('exam_source').choices)
    fields = ['exam_source_manual', 'exam_source', 'difficulty', 'original_difficulty']
    if 'exam_source' in request.POST:
        code = request.POST['exam_source']
        if code and code not in sources:
            return JsonResponse({'error': 'Неизвестный источник.'}, status=400)
        if 'difficulty' in request.POST:
            task.exam_source = code
        else:
            apply_source(task, code)  # an official source brings the «ЕГЭ» level, another one gives the own level back
    if 'difficulty' in request.POST:
        if request.POST['difficulty'] not in LEVELS:
            return JsonResponse({'error': 'Неизвестный уровень.'}, status=400)
        task.difficulty = request.POST['difficulty']
    task.exam_source_manual = True
    task.save(update_fields=fields)
    return JsonResponse({'difficulty': task.difficulty, 'difficulty_label': task.get_difficulty_display(),
                         'exam_source': task.exam_source, 'exam_source_label': task.get_exam_source_display()})
