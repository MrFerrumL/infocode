"""«Тренировка дня» page: the pupil's personal set, solved right on the page."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from .daily_practice import daily_tasks
from .models import Bookmark, Practice
from .views import card_queryset, practice_answer, prepare_cards


def daily_progress(user):
    picked = daily_tasks(user)
    ids = [pk for pk, _ in picked]
    done = set(Practice.objects.filter(user=user, correct=True, task_id__in=ids).values_list('task_id', flat=True))
    return picked, done


@login_required
def daily(request):
    submitted, answer_form, response = practice_answer(request)
    if response:
        return response
    picked, done = daily_progress(request.user)
    reasons = dict(picked)
    order = {pk: i for i, (pk, _) in enumerate(picked)}
    cards = sorted(card_queryset(request.user).filter(id__in=order), key=lambda t: order[t.pk])
    saved = set(Bookmark.objects.filter(user=request.user).values_list('task_id', flat=True))
    prepare_cards(cards, request.user, done, saved, submitted, answer_form)
    for card in cards:
        card.daily_reason = reasons[card.pk]
    return render(request, 'daily.html', {'cards': cards, 'done': len(done), 'size': len(picked), 'nav': 'daily'})
