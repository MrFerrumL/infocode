"""Read-only API for the separate test platform (КР): it composes variants from this task bank.

Access needs the shared secret KR_API_TOKEN (header "Authorization: Bearer <token>"); without it the API is off.
- GET  /api/kr/numbers/?subject=inf      → numbers with the count of tasks that have a known answer;
- POST /api/kr/compose/                  → {"subject", "numbers": [{"number", "count"}], "variants"} → variants
                                            without repeated tasks (inside a variant and between variants);
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


@csrf_exempt
@require_GET
@guarded
def numbers(request):
    code = request.GET.get('subject', 'inf')
    if code not in SUBJECTS:
        raise Http404
    with using(code):
        counts = {}
        for number in pool().values_list('topic__number', flat=True):
            counts[number] = counts.get(number, 0) + 1
    return JsonResponse({'subject': code, 'title': SUBJECTS[code]['title'], 'numbers': [{'number': n, 'count': c} for n, c in sorted(counts.items())]})


@csrf_exempt
@require_POST
@guarded
def compose(request):
    try:
        spec = json.loads(request.body)
        code = spec.get('subject', 'inf')
        variants = int(spec.get('variants', 1))
        wanted = [(int(row['number']), int(row['count'])) for row in spec.get('numbers', [])]
    except (ValueError, TypeError, KeyError, AttributeError):
        return JsonResponse({'error': 'Неверный запрос.'}, status=400)
    if code not in SUBJECTS or not 1 <= variants <= MAX_VARIANTS or not wanted or any(not 1 <= c <= MAX_PER_NUMBER for _, c in wanted):
        return JsonResponse({'error': f'Можно до {MAX_VARIANTS} вариантов и до {MAX_PER_NUMBER} задач на номер.'}, status=400)
    rng = random.SystemRandom()
    with using(code):
        result = [[] for _ in range(variants)]
        for number, count in wanted:
            ids = list(pool().filter(topic__number=number).values_list('pk', flat=True))
            need = count * variants
            if len(ids) < need:
                return JsonResponse({'error': f'Для № {number} в банке {len(ids)} задач с ответом, а нужно {need} (без повторов).'}, status=400)
            picked = rng.sample(ids, need)
            tasks = {t.pk: t for t in Task.objects.filter(pk__in=picked).select_related('topic')}
            for v in range(variants):
                result[v].extend(task_json(tasks[pk], request) for pk in picked[v * count:(v + 1) * count])
    return JsonResponse({'subject': code, 'variants': result})


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
