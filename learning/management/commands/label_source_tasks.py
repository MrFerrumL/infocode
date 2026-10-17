"""Set KOMPEGE-style notes on imported tasks from a prepared JSON list.

Each entry: {"system": "kompege", "topic": 7, "id": 31372, "label": "Авторская · Л. Шастин", "kind": "author"}.
Only the note and kind change; conditions, answers and pupils' history stay as they are.
"""
import json
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from learning.models import Task
from learning.sources import KINDS, SITES


class Command(BaseCommand):
    help = 'Update source notes ("Демоверсия 2027", "Авторская · …") of imported tasks.'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, path, dry_run=False, **options):
        try:
            with open(path, encoding='utf-8') as handle:
                rows = json.load(handle)
            kinds = {k for k, _ in KINDS}
            for row in rows:
                if row['system'] not in SITES or not row['system'] or type(row['topic']) is not int or type(row['id']) is not int \
                        or row['kind'] not in kinds or not isinstance(row['label'], str) or not 0 < len(row['label']) <= 120:
                    raise ValueError(f'Invalid entry: {row}')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CommandError('Label list rejected: ' + str(exc)) from exc
        wanted = {(r['system'], r['topic'], r['id']): (r['label'], r['kind']) for r in rows}
        tasks = Task.all_objects.filter(source_system__in={k[0] for k in wanted}, external_id__in={k[2] for k in wanted}).select_related('topic')
        changes = []
        for task in tasks:
            value = wanted.get((task.source_system, task.topic.number, task.external_id))
            if value and (task.source_label, task.source_kind) != value:
                task.source_label, task.source_kind = value
                changes.append(task)
        self.stdout.write(f'{len(rows)} notes; {len(changes)} tasks to update.')
        if dry_run:
            self.stdout.write('Dry run: nothing changed.')
            return
        with transaction.atomic():
            Task.all_objects.bulk_update(changes, ['source_label', 'source_kind'], batch_size=500)
        self.stdout.write(self.style.SUCCESS(f'Updated notes of {len(changes)} tasks.'))
