"""Collections section: step-by-step sets solved right on the page."""
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
    own = list(Collection.objects.filter(source='infocode', published=True).annotate(size=Count('items')).prefetch_related('items'))
    numbers = {}
    for collection in own:
        collection.done = sum(1 for item in collection.items.all() if item.task_id in solved)
        numbers.setdefault(collection.number, []).append(collection)
    rows = [{'number': '19–21' if n == 19 else str(n), 'own': items} for n, items in sorted(numbers.items())]
    return render(request, 'collections.html', {'rows': rows, 'nav': 'collections'})


@login_required
def collection_detail(request, pk):
    collection = get_object_or_404(Collection, pk=pk, source='infocode', published=True)
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
