from collections import Counter
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Prefetch
from learning import embeddings, seventeen
from learning.models import Task, TaskBlock


class Command(BaseCommand):
    help = ('Mark № 17 tasks of informatics by what the question enumerates: pairs, triples or quadruples of elements '
            '(learning/seventeen.py). The mark is the task\'s subtopic «Перебор пар / троек / четвёрок», which the catalogue '
            'and the КР picker filter by; a subtopic a teacher typed is never changed. --revert removes the automatic marks.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--revert', action='store_true')

    @transaction.atomic
    def handle(self, *args, **opts):
        tasks = (Task.all_objects.filter(topic__number=17, topic__subject='inf', archived_at__isnull=True).select_related('text_vector')
                 .prefetch_related(Prefetch('content_blocks', queryset=TaskBlock.all_objects.filter(kind__in=['text', 'html', 'heading']))))
        changed, per_kind = 0, Counter()
        for task in tasks.iterator(chunk_size=500):
            if opts['revert']:
                mark = '' if task.subtopic in seventeen.SUBTOPICS.values() else None
            else:
                row = getattr(task, 'text_vector', None)
                mark = seventeen.subtopic_for(task, embeddings.task_text(task, row.source_text if row else ''))
            if mark is None:
                per_kind['subtopic of a teacher, kept'] += 1
                continue
            per_kind[mark or 'no mark'] += 1
            if mark != task.subtopic:
                changed += 1
                if not opts['dry_run']:
                    Task.all_objects.filter(pk=task.pk).update(subtopic=mark)
        self.stdout.write('; '.join(f'{kind}: {n}' for kind, n in sorted(per_kind.items())))
        self.stdout.write(f'Tasks changed: {changed}' + (' (dry run, nothing saved)' if opts['dry_run'] else '') + '.')
