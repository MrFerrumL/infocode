"""Read-only API for the separate test platform (КР): it composes variants from this task bank.

Access needs the shared secret KR_API_TOKEN (header "Authorization: Bearer <token>"); without it the API is off.
- GET  /api/kr/numbers/?subject=inf      → numbers with the count of tasks that have a known answer;
- POST /api/kr/compose/                  → {"subject", "numbers": [{"number", "count", "source", "ids"}], "variants"} →
                                            variants. Source "bank" (default): random tasks without repeats inside a
                                            variant and between variants; "pick": the teacher's chosen task ids;
                                            "generate": new tasks made by learning/task_generators;
                                            "level" keeps one difficulty; {"exact": [[ids], …]} sends ready variants;
- GET  /api/kr/asset/<block id>/          → a picture or data file of a task condition.
Only published catalogue tasks with an answer are used; 20/21 of informatics 19–21 groups are not split off.
"""
import hmac
import json
import random
from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from . import game_groups
from .models import Task, TaskBlock
from .subjects import SUBJECTS, using

MAX_PER_NUMBER = 10
MAX_VARIANTS = 30


def authorised(request):
    token = getattr(settings, 'KR_API_TOKEN', '')
    given = request.headers.get('Authorization', '').removeprefix('Bearer ').strip()
    return bool(token) and hmac.compare_digest(token, given)


def guarded(view):
    def wrapper(request, *args, **kwargs):
        if not authorised(request):
            return JsonResponse({'error': 'unauthorised'}, status=401)
        return view(request, *args, **kwargs)
    wrapper.__name__ = view.__name__
    return wrapper


def pool():
    return (Task.objects.filter(published=True, in_catalog=True, code_mode__in=['off', 'optional']).exclude(answer='')
            .exclude(game_groups.grouped_follow_up()))


def task_json(task, request):
    blocks = []
    if task.statement.strip():
        blocks.append({'kind': 'text', 'text': task.statement})
    for block in task.content_blocks.filter(purpose='statement').order_by('position', 'id'):
        item = {'kind': block.kind, 'text': block.text}
        if block.kind in ('image', 'file'):
            item['url'] = request.build_absolute_uri(reverse('kr_api_asset', args=[block.pk]))
            item['name'] = (block.image or block.file).name.rsplit('/', 1)[-1]
        blocks.append(item)
    if task.attachment:
        blocks.append({'kind': 'file', 'text': 'Данные к заданию', 'name': task.attachment.name.rsplit('/', 1)[-1],
                       'url': request.build_absolute_uri(reverse('kr_api_asset', args=[f'a{task.pk}']))})
    return {'id': task.pk, 'number': task.topic.number, 'answer': task.answer, 'source': task.source_label or task.source,
            'title': task.title, 'blocks': blocks}


def available(code):
    """{number: tasks with a known answer} of a subject."""
    with using(code):
        counts = {}
        for number in pool().values_list('topic__number', flat=True):
            counts[number] = counts.get(number, 0) + 1
    return counts


@csrf_exempt
@require_GET
@guarded
def numbers(request):
    code = request.GET.get('subject', 'inf')
    if code not in SUBJECTS:
        raise Http404
    counts = available(code)
    return JsonResponse({'subject': code, 'title': SUBJECTS[code]['title'], 'numbers': [{'number': n, 'count': c} for n, c in sorted(counts.items())]})


@csrf_exempt
@require_POST
@guarded
def compose(request):
    try:
        spec = json.loads(request.body)
        code = spec.get('subject', 'inf')
        variants = int(spec.get('variants', 1))
        level = spec.get('level') or None
        exact = [[int(i) for i in ids][:MAX_PICKED] for ids in spec.get('exact', [])][:MAX_VARIANTS]
        wanted = [{'number': int(row['number']), 'count': int(row['count']), 'source': row.get('source', 'bank'),
                   'ids': [int(i) for i in row.get('ids', [])][:MAX_PICKED]} for row in spec.get('numbers', [])]
    except (ValueError, TypeError, KeyError, AttributeError):
        return JsonResponse({'error': 'Неверный запрос.'}, status=400)
    if exact and code in SUBJECTS:
        # Variants the teacher has already put together task by task.
        with using(code):
            tasks = {t.pk: t for t in Task.all_objects.filter(pk__in={i for ids in exact for i in ids}, published=True)
                     .exclude(answer='').select_related('topic')}
        if any(i not in tasks for ids in exact for i in ids) or not all(exact):
            return JsonResponse({'error': 'Некоторых задач варианта больше нет в банке — обновите черновик.'}, status=400)
        return JsonResponse({'subject': code, 'variants': [[task_json(tasks[i], request) for i in ids] for ids in exact]})
    if level not in (None, *LEVELS):
        return JsonResponse({'error': 'Неизвестный уровень сложности.'}, status=400)
    if code not in SUBJECTS or not 1 <= variants <= MAX_VARIANTS or not wanted             or any(not 1 <= row['count'] <= MAX_PER_NUMBER or row['source'] not in SOURCES for row in wanted):
        return JsonResponse({'error': f'Можно до {MAX_VARIANTS} вариантов и до {MAX_PER_NUMBER} задач на номер.'}, status=400)
    rng = random.SystemRandom()
    with using(code):
        result = [[] for _ in range(variants)]
        for row in wanted:
            try:
                chosen = SOURCES[row['source']](row['number'], row['count'], variants, row['ids'], rng, level)
            except ComposeError as exc:
                return JsonResponse({'error': str(exc)}, status=400)
            tasks = {t.pk: t for t in Task.all_objects.filter(pk__in=chosen).select_related('topic')}
            count = row['count']
            for v in range(variants):
                result[v].extend(task_json(tasks[pk], request) for pk in chosen[v * count:(v + 1) * count])
    return JsonResponse({'subject': code, 'variants': result})


class ComposeError(Exception):
    pass


def from_bank(number, count, variants, ids, rng, level=None, exclude=()):
    """Random tasks of the bank (of one difficulty if asked), none repeated anywhere."""
    found = pool().filter(topic__number=number).exclude(pk__in=exclude)
    if level:
        found = found.filter(difficulty=level)
    found = list(found.values_list('pk', flat=True))
    need = count * variants
    if len(found) < need:
        what = f' уровня «{LEVELS[level]}»' if level else ''
        raise ComposeError(f'Для № {number} в банке {len(found)} задач{what} с ответом, а нужно {need} (без повторов).')
    return rng.sample(found, need)


def from_picked(number, count, variants, ids, rng, level=None):
    """Tasks the teacher chose in the catalogue; variants share them only when too few were chosen."""
    found = list(pool().filter(topic__number=number, pk__in=ids).values_list('pk', flat=True))
    if len(found) < count:
        raise ComposeError(f'Для № {number} выбрано задач: {len(found)}, а в каждом варианте нужно {count}.')
    rng.shuffle(found)
    return [found[i % len(found)] for i in range(count * variants)]


def generated(number, count, variants, ids, rng, level=None):
    """New tasks made right now by the generators (kept in the bank for variants only, outside the catalogue)."""
    from .models import Topic
    from .task_generators import REGISTRY, generate, save, taken_in
    if number not in REGISTRY:
        raise ComposeError(f'Для № {number} пока нет генератора задач.')
    topic = Topic.objects.get(number=number)
    need = count * variants
    made = generate(number, need * (6 if level else 1), seed=rng.randrange(1 << 30), taken=taken_in(topic))
    made = [t for t in made if not level or t.difficulty == level][:need]
    if len(made) < need:
        raise ComposeError(f'Генератор № {number} смог сделать только {len(made)} новых задач из {need}.')
    return [task.pk for task in save(made, topic, published=True, in_catalog=False)]


SOURCES = {'bank': from_bank, 'pick': from_picked, 'generate': generated}
LEVELS = dict(Task.LEVELS)
MAX_PICKED = 300


@require_GET
@guarded
def asset(request, ref):
    if str(ref).startswith('a'):
        task = Task.all_objects.filter(pk=int(ref[1:]) if ref[1:].isdigit() else 0).first()
        if not task or not task.attachment:
            raise Http404
        stored = task.attachment
    else:
        block = TaskBlock.all_objects.filter(pk=int(ref) if str(ref).isdigit() else 0, kind__in=['image', 'file']).first()
        if not block:
            raise Http404
        stored = block.image if block.kind == 'image' else block.file
    return FileResponse(stored.open('rb'), as_attachment=False, filename=stored.name.rsplit('/', 1)[-1])
