import re
from django.core.management.base import BaseCommand
from django.db import transaction
from learning import embeddings
from learning.models import Task, TaskBlock, Topic
from learning.subjects import using

TAG = 'старый-формат'
# What the ЕГЭ-2027 task of the number looks like; anything else with a known text is the old format.
NEW_FORMAT = {
    12: ('машина Тьюринга', re.compile(r'тьюринг|\bМТ\b|головк\w*|бесконечн\w* лент', re.I)),
    23: ('ациклический взвешенный граф в файле', re.compile(r'ациклическ|взвешенн\w* граф|ориентированн\w* граф', re.I)),
}
# Old tasks that are a current format of another number move there instead of leaving the catalogue:
# counting executor programs was № 23 and is № 13 (dynamic programming) since ЕГЭ-2027.
MOVE = {23: (13, re.compile(r'исполнител', re.I), re.compile(r'программ|результат|чисел может|последовательност', re.I))}


class Command(BaseCommand):
    help = ('Keep only the ЕГЭ-2027 formats in № 12 (Turing machine) and № 23 (weighted graph in a file). Counting executor '
            'programs, filed under № 23, moves to № 13 where it belongs now; other old tasks (string editor, robot) leave '
            f'the catalogue but stay published (variants, history, courses keep them) and are tagged «{TAG}»; --restore '
            'brings those back. Tasks whose text is unknown (pictures only) are kept and listed.')

    def add_arguments(self, parser):
        parser.add_argument('--numbers', nargs='*', type=int, default=sorted(NEW_FORMAT))
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--restore', action='store_true')

    def handle(self, *args, **opts):
        with using('inf'):
            if opts['restore']:
                return self.restore(opts)
            for number in opts['numbers']:
                self.retire(number, opts['dry_run'])

    def retire(self, number, dry_run):
        title, pattern = NEW_FORMAT[number]
        tasks = (Task.objects.filter(topic__number=number, published=True, in_catalog=True).select_related('text_vector')
                 .prefetch_related(_text_blocks()))
        old, moved, kept, unknown = [], [], 0, []
        target, *move_patterns = MOVE.get(number, (None,))
        for task in tasks:
            row = getattr(task, 'text_vector', None)
            text = embeddings.task_text(task, row.source_text if row else '')
            if not text:
                unknown.append(task.pk)
            elif pattern.search(text):
                kept += 1
            elif target and all(p.search(text) for p in move_patterns):
                moved.append(task.pk)
            else:
                old.append(task.pk)
        move = f'moved to № {target}: {len(moved)}; ' if target else ''
        self.stdout.write(f'№ {number}: new format «{title}» kept {kept}; {move}old format hidden {len(old)}; '
                          f'without text, kept for a look: {len(unknown)} {unknown[:20]}' + (' (dry run)' if dry_run else ''))
        if dry_run:
            return
        with transaction.atomic():
            if moved:
                new_topic = Topic.objects.get(number=target)
                for task in Task.objects.filter(pk__in=moved):
                    clash = task.external_id is not None and Task.all_objects.filter(
                        source_system=task.source_system, external_id=task.external_id, topic=new_topic).exists()
                    if clash:
                        old.append(task.pk)
                        continue
                    task.topic = new_topic
                    task.title = task.title.replace(f'№ {number} ·', f'№ {target} ·')
                    task.save(update_fields=['topic', 'title'])
            for task in Task.objects.filter(pk__in=old):
                task.tags = ', '.join([t for t in (x.strip() for x in task.tags.split(',')) if t and t != TAG] + [TAG])
                task.in_catalog = False
                task.save(update_fields=['tags', 'in_catalog'])

    def restore(self, opts):
        restored = 0
        with transaction.atomic():
            for task in Task.objects.filter(topic__number__in=opts['numbers'], in_catalog=False, tags__icontains=TAG):
                task.tags = ', '.join(t for t in (x.strip() for x in task.tags.split(',')) if t and t != TAG)
                task.in_catalog = True
                if not opts['dry_run']:
                    task.save(update_fields=['tags', 'in_catalog'])
                restored += 1
        self.stdout.write(f'Back in the catalogue: {restored}' + (' (dry run)' if opts['dry_run'] else ''))


def _text_blocks():
    from django.db.models import Prefetch
    return Prefetch('content_blocks', queryset=TaskBlock.all_objects.filter(kind__in=['text', 'html', 'heading']))
