"""Collections section: own sets and imported Yandex Uchebnik sets solved on the page; not yet imported Yandex sets as links."""
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import get_object_or_404, render

from .collection_models import Collection
from .models import Bookmark, Practice
from .views import card_queryset, practice_answer, prepare_cards


def solved_ids(user):
    return set(Practice.objects.filter(user=user, correct=True).values_list('task_id', flat=True))


@login_required
def collections(request):
    solved = solved_ids(request.user)
    numbers, yandex = {}, {}
    for collection in Collection.objects.filter(published=True).annotate(size=Count('items')).prefetch_related('items'):
        collection.done = sum(1 for item in collection.items.all() if item.task_id in solved)
        (yandex if collection.source == 'yandex' else numbers).setdefault(collection.number, []).append(collection)
    # One row per exam number: the platform's sets next to Yandex Uchebnik's.
    rows = [{'number': '19–21' if n == 19 else str(n), 'own': numbers.get(n, []), 'yandex': yandex.get(n, [])}
            for n in sorted(set(numbers) | set(yandex))]
    return render(request, 'collections.html', {'rows': rows, 'nav': 'collections'})


@login_required
def collection_detail(request, pk):
    collection = get_object_or_404(Collection.objects.filter(items__isnull=False).distinct(), pk=pk, published=True)
    submitted, answer_form, response = practice_answer(request)
    if response:
        return response
    order = {task_id: position for task_id, position in collection.items.values_list('task_id', 'position')}
    cards = sorted(card_queryset(request.user).filter(id__in=order, published=True, in_catalog=True), key=lambda t: order[t.pk])
    solved = solved_ids(request.user)
    saved = set(Bookmark.objects.filter(user=request.user).values_list('task_id', flat=True))
    prepare_cards(cards, request.user, solved, saved, submitted, answer_form)
    return render(request, 'collection.html', {'collection': collection, 'cards': cards,
        'done': sum(1 for t in cards if t.pk in solved), 'nav': 'collections'})
