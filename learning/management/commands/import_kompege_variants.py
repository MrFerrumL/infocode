"""Import KOMPEGE exam variants: hidden tasks first, then variants scored like the exam.

Tasks already in the catalogue are referenced by (number, source ID); tasks the source keeps out
of its bank are created with in_catalog=False and are reachable only through variants. Existing
variants (same KIM) and tasks are skipped, so the command can be repeated safely.
"""
import re
import zipfile
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from PIL import Image
from learning.models import Task, Topic, Variant, VariantItem
from learning.topic_defaults import TOPICS
from .import_kompege_numbers import VARIANTS_FORMAT, create_task, inspect_bundle

OFFICIAL = 'Открытые пробники и реальные варианты'


def variant_title(row):
    """Self-explanatory titles: 'ЕГЭ 2026 · Основная волна 18.06.26', 'ЕГЭ 2026 · Вариант #3 от Л. Шастина'."""
    title = f'ЕГЭ {row["exam_year"]} · {row["title"]}'
    collection = row['collection']
    if collection.startswith('Варианты от '):
        title += ' от ' + collection[len('Варианты от '):]
    return title[:180]


def check_variants(manifest):
    seen = set()
    for row in manifest.get('variants') or []:
        kim = row.get('kim')
        if type(kim) is not int or kim in seen or not re.fullmatch(r'\d{4}/\d{2}', str(row.get('school_year'))):
            raise ValueError('Invalid variant identity or school year')
        seen.add(kim)
        if not isinstance(row.get('title'), str) or not 0 < len(row['title']) <= 120 or not isinstance(row.get('collection'), str) or len(row['collection']) > 150:
            raise ValueError(f'Invalid variant title: {kim}')
        if row.get('source_url') != f'https://kompege.ru/variant?kim={kim}' or not isinstance(row.get('items'), list) or not 1 <= len(row['items']) <= 40:
            raise ValueError(f'Invalid variant source or size: {kim}')
        for position, item in enumerate(row['items'], 1):
            if item.get('position') != position or any(type(item.get(k)) is not int for k in ('number', 'topic', 'source_id', 'rows', 'cols')):
                raise ValueError(f'Invalid variant item: {kim}/{position}')
            if not (1 <= item['number'] <= 27 and 1 <= item['topic'] <= 27 and 1 <= item['rows'] <= 20 and 1 <= item['cols'] <= 10):
                raise ValueError(f'Variant item out of range: {kim}/{position}')
            if not isinstance(item.get('answer'), str) or not item['answer'].strip() or len(item['answer']) > 250:
                raise ValueError(f'Variant item needs an answer: {kim}/{position}')
    if not seen:
        raise ValueError('No variants in the bundle')


class Command(BaseCommand):
    help = 'Import KOMPEGE exam variants with their hidden tasks; existing variants and tasks are kept.'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--publish', action='store_true', help='Publish new variants for all pupils')

    def handle(self, path, dry_run=False, publish=False, **options):
        try:
            with zipfile.ZipFile(path) as archive:
                manifest, assets = inspect_bundle(archive)
            if manifest.get('format') != VARIANTS_FORMAT:
                raise ValueError('Expected a variants bundle')
            check_variants(manifest)
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, Image.DecompressionBombError) as exc:
            raise CommandError('Variant validation failed: ' + str(exc)) from exc
        rows, variants = manifest['tasks'], manifest['variants']
        existing = set(Task.all_objects.filter(source_system='kompege').values_list('topic__number', 'external_id'))
        bundled = {(row['number'], row['source_id']) for row in rows}
        needed = {(item['topic'], item['source_id']) for v in variants for item in v['items']}
        missing = needed - existing - bundled
        if missing:
            raise CommandError(f'{len(missing)} referenced catalogue tasks are not on this site, e.g. {sorted(missing)[:5]}. Import the bank first.')
        known = set(Variant.objects.filter(source_system='kompege').values_list('external_id', flat=True))
        new_tasks = [row for row in rows if (row['number'], row['source_id']) not in existing]
        new_variants = [v for v in variants if v['kim'] not in known]
        self.stdout.write(f'Validated {len(variants)} variants ({len(new_variants)} new) and {len(rows)} hidden tasks ({len(new_tasks)} new).')
        if dry_run:
            self.stdout.write('Dry run: no database or media changes.')
            return
        owner = get_user_model().objects.filter(is_superuser=True, is_active=True).order_by('pk').first()
        if owner is None:
            raise CommandError('An active administrator is required to own imported variants.')
        written = []
        try:
            with transaction.atomic():
                topics = {}
                for number in range(1, 28):
                    topics[number], _ = Topic.objects.get_or_create(number=number, defaults={'title': TOPICS[number-1]})
                for row in new_tasks:
                    create_task(row, topics[row['number']], True, assets, path, written, in_catalog=False,
                                tags=f'КОМПЕГЭ, задание из варианта №{row["number"]}')
                tasks = {(t.topic.number, t.external_id): t for t in Task.all_objects.filter(source_system='kompege', external_id__in={k[1] for k in needed})
                         .select_related('topic').prefetch_related('content_blocks')}
                for row in new_variants:
                    variant = Variant.objects.create(title=variant_title(row), kind='exam', creator=owner, duration=235, published=publish,
                        scoring='ege', hide_answers=row['hide_answers'], school_year=row['school_year'], collection=row['collection'],
                        source_system='kompege', external_id=row['kim'], source_url=row['source_url'], sort_order=row['order'])
                    items = []
                    for item in row['items']:
                        task = tasks[(item['topic'], item['source_id'])]
                        items.append(VariantItem(variant=variant, task=task, position=item['position'], number=item['number'],
                            statement=task.statement, answer=item['answer'], code_mode='off', solution=task.solution,
                            attachment=task.attachment.name, answer_rows=item['rows'], answer_cols=item['cols'],
                            blocks=[b.snapshot() for b in task.content_blocks.all() if not b.archived_at]))
                    VariantItem.objects.bulk_create(items)
                    variant.topics.set({topics[item['number']].pk for item in row['items']})
        except Exception:
            for name in written:
                default_storage.delete(name)
            raise
        self.stdout.write(self.style.SUCCESS(f'Variants imported: {len(new_variants)} new variants, {len(new_tasks)} hidden tasks. Existing data preserved.'))
