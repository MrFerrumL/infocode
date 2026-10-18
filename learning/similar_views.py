"""The "Похожие" button under a task card: similar tasks shown below it and solved in place."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from .models import Bookmark, Practice, Task
from .similar_tasks import similar_to
from .views import card_queryset, practice_answer, prepare_cards


@login_required
def similar(request, pk):
    # Cards shown here post their answers back to this URL.
    submitted, answer_form, response = practice_answer(request)
    if response:
        return response
    task = get_object_or_404(Task.objects.select_related('topic'), pk=pk, published=True, in_catalog=True)
    ids, how = similar_to(task, request.user)
    order = {pk: i for i, pk in enumerate(ids)}
    cards = sorted(card_queryset(request.user).filter(id__in=ids), key=lambda t: order[t.pk])
    solved = set(Practice.objects.filter(user=request.user, correct=True).values_list('task_id', flat=True))
    saved = set(Bookmark.objects.filter(user=request.user).values_list('task_id', flat=True))
    prepare_cards(cards, request.user, solved, saved, submitted, answer_form)
    return render(request, 'partials/similar_tasks.html', {'cards': cards, 'how': how, 'task': task})
