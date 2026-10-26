"""Name informatics headings 10, 13 and 23 as in ЕГЭ-2027 and move the site's introductory tasks with them.

KOMPEGE, FIPI and K. Polyakov already file IP masks under 10, counting programs under 13 and graphs under 23;
only the site's headings and seed_demo's «… · практика N» tasks kept the 2026 numbering.

- A heading is renamed only while it still has the other scheme's default name: a teacher's own name stays.
- «IP-адреса и маски сетей · практика N» go from 13 to 10. If 10 already has the same task (seed_demo ran
  first), the one in 13 is hidden instead.
- «Поиск в текстовых документах · практика N» (text search left the exam) are hidden.
Hidden means in_catalog=False and the tag «старый-формат»: the task stays published, in works and with every
pupil's answers. --reverse renames the headings back, returns hidden tasks and moves the IP tasks to 13.
«Подсчёт программ · практика N» (23 → 13) are moved by retire_old_formats.
"""
import re

from django.core.management.base import BaseCommand
from django.db import transaction

from learning.models import Task, Topic
from learning.topic_defaults import TOPICS, TOPICS_2026

OLD_FORMAT = 'старый-формат'
IP, TEXT_SEARCH = TOPICS_2026[13], TOPICS_2026[10]


def practice(topic, title):
    return Task.all_objects.filter(topic=topic, source_system='', title__regex=rf'^{re.escape(title)} · практика \d+$')


def hide(task):
    tags = [t.strip() for t in task.tags.split(',') if t.strip()]
    task.in_catalog, task.tags = False, ', '.join(tags if OLD_FORMAT in tags else tags + [OLD_FORMAT])
    task.save(update_fields=['in_catalog', 'tags'])


def show(task):
    task.in_catalog = True
    task.tags = ', '.join(t.strip() for t in task.tags.split(',') if t.strip() and t.strip() != OLD_FORMAT)
    task.save(update_fields=['in_catalog', 'tags'])


def move(tasks, target):
    """Move tasks to target unless it has one of the same title; return (moved, left behind)."""
    present = set(practice(target, IP).values_list('title', flat=True))
    moved, left = 0, []
    for task in tasks:
        if task.title in present:
            left.append(task)
        else:
            task.topic = target
            task.save(update_fields=['topic'])
            moved += 1
    return moved, left


class Command(BaseCommand):
    help = 'Rename informatics headings 10, 13, 23 to ЕГЭ-2027 and move the introductory tasks; --reverse undoes it.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report the changes and roll them back.')
        parser.add_argument('--reverse', action='store_true', help='Go back to the ЕГЭ-2026 headings.')

    @transaction.atomic
    def handle(self, dry_run=False, reverse=False, **options):
        topics = {t.number: t for t in Topic.objects.select_for_update().filter(number__in=TOPICS_2026)}
        old, new = TOPICS_2026, {n: TOPICS[n - 1] for n in TOPICS_2026}
        if reverse:
            old, new = new, old
        for number, topic in sorted(topics.items()):
            if topic.title == new[number]:
                self.stdout.write(f'№{number}: already «{topic.title}».')
            elif topic.title == old[number]:
                self.stdout.write(f'№{number}: «{topic.title}» → «{new[number]}».')
                topic.title = new[number]
                topic.save(update_fields=['title'])
            else:
                self.stdout.write(f'№{number}: own heading «{topic.title}» kept.')
        if 10 in topics and 13 in topics:
            (self.backward if reverse else self.forward)(topics[10], topics[13])
        if dry_run:
            transaction.set_rollback(True)
            self.stdout.write('Dry run: nothing saved.')

    def forward(self, ten, thirteen):
        moved, left = move(practice(thirteen, IP), ten)
        for task in left:
            hide(task)
        searches = list(practice(ten, TEXT_SEARCH).filter(in_catalog=True))
        for task in searches:
            hide(task)
        self.stdout.write(f'«{IP} · практика»: №13 → №10: {moved}; hidden in №13 as copies of tasks in №10: {len(left)}.')
        self.stdout.write(f'«{TEXT_SEARCH} · практика» in №10 hidden from the catalogue: {len(searches)}.')

    def backward(self, ten, thirteen):
        returned = list(practice(thirteen, IP).filter(tags__contains=OLD_FORMAT)) + list(practice(ten, TEXT_SEARCH).filter(tags__contains=OLD_FORMAT))
        for task in returned:
            show(task)
        moved, left = move(practice(ten, IP), thirteen)
        self.stdout.write(f'Back in the catalogue: {len(returned)}. «{IP} · практика»: №10 → №13: {moved}; '
                          f'kept in №10 (№13 has the same task): {len(left)}.')
