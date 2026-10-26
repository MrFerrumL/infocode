from collections import Counter
from django.core.management.base import BaseCommand
from django.db import transaction
from learning import exam_sources
from learning.models import Task, TaskVector


class Command(BaseCommand):
    help = ('Give every task its exam source (demo, approbation, waves, ЕГКР, open variant, FIPI…) from the source note '
            'or the bracket opening the condition; copies of labelled tasks get the same source by their sentence vector. '
            'Official sources make the «ЕГЭ» level (the task\'s own level is kept). Teachers\' choices are never changed. '
            '--revert puts every level back.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--revert', action='store_true')

    @transaction.atomic
    def handle(self, *args, **opts):
        tasks = Task.all_objects.filter(published=True, exam_source_manual=False)
        if opts['revert']:
            restored = 0
            for task in tasks.exclude(original_difficulty=''):
                task.difficulty, task.original_difficulty, task.exam_source = task.original_difficulty, '', ''
                task.save(update_fields=['difficulty', 'original_difficulty', 'exam_source'])
                restored += 1
            tasks.update(exam_source='')
            self.stdout.write(f'Levels put back: {restored}.')
            return
        texts = dict(TaskVector.objects.values_list('task_id', 'source_text'))
        codes, how = {}, Counter()
        for task in tasks.only('pk', 'source_label', 'statement'):
            code = exam_sources.classify(task.source_label, task.statement or texts.get(task.pk, ''))
            codes[task.pk] = code
            how['by note' if code else 'no note'] += 1
        # Copies of labelled tasks in another bank inherit the source (same text by the vector, same answer).
        rows = {pk: (n, a, v) for pk, n, a, v in Task.all_objects.filter(pk__in=list(codes)).values_list(
            'pk', 'topic__number', 'answer', 'text_vector__vector')}
        labelled = [(pk, *rows[pk], code) for pk, code in codes.items() if code]
        unlabelled = [(pk, *rows[pk]) for pk, code in codes.items() if not code]
        inherited = exam_sources.copies(unlabelled, labelled)
        codes.update(inherited)
        how['copies of labelled tasks'] = len(inherited)
        changed, per_source = 0, Counter()
        for task in tasks.filter(pk__in=list(codes)):
            code = codes[task.pk]
            per_source[code or '—'] += 1
            if exam_sources.apply(task, code):
                changed += 1
                if not opts['dry_run']:
                    task.save(update_fields=['exam_source', 'difficulty', 'original_difficulty'])
        labels = dict(Task._meta.get_field('exam_source').choices)
        self.stdout.write(', '.join(f'{k}: {v}' for k, v in how.items()))
        self.stdout.write('; '.join(f'{labels.get(k, "без источника")}: {v}' for k, v in per_source.most_common()))
        official = sum(v for k, v in per_source.items() if k in exam_sources.OFFICIAL)
        self.stdout.write(f'Level «ЕГЭ» (official sources): {official}; tasks changed: {changed}' + (' (dry run)' if opts['dry_run'] else ''))
        if opts['dry_run']:
            transaction.set_rollback(True)
