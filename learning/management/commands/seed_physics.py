"""Fill the physics platform from learning/data/physics (deploy/build_physics_bank.py): numbers 1–26, the platform's
own tasks with solutions, theory notes, collections and training variants. Safe to run again: tasks are matched by
their ID and updated; pupils' answers stay.
"""
import json
import re
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.db import transaction
from learning.collection_models import Collection, CollectionItem
from learning.ipynb_import import to_html
from learning.models import ExamAttempt, Task, TaskBlock, Topic, Variant, VariantItem
from learning.notebook_models import Notebook, NotebookCell
from learning.subjects import using

DATA = Path(__file__).resolve().parents[2] / 'data' / 'physics'
SYSTEM = 'infocode-phys'
LEVELS = [('easy', 'знакомство', 'Задачи попроще — чтобы разобраться в номере.'),
          ('medium', 'тренировка', 'Задачи уровня экзамена.'),
          ('hard', 'на подумать', 'Задачи посложнее и задачи второй части.')]
VARIANTS = 3


class Command(BaseCommand):
    help = 'Create or update the physics platform content (numbers, tasks, notes, collections, variants).'

    def handle(self, **options):
        bank = json.loads((DATA / 'bank.json').read_text(encoding='utf-8'))
        owner = get_user_model().objects.filter(is_superuser=True, is_active=True).order_by('pk').first()
        with using('phys'), transaction.atomic():
            topics = {}
            for number, title in bank['topics'].items():
                topics[int(number)], _ = Topic.objects.update_or_create(number=int(number), defaults={'title': title})
            created = updated = 0
            by_number = {}
            for row in bank['tasks']:
                task = Task.all_objects.filter(source_system=SYSTEM, external_id=row['id']).first()
                fields = {'topic': topics[row['number']], 'title': f'Физика · задача {row["number"]}.{row["id"] % 1000}',
                          'statement': row['statement'], 'answer': row['answer'], 'solution': row['solution'], 'difficulty': row['difficulty'],
                          'source': 'Инфокод · авторская задача', 'source_label': 'Авторская · Инфокод', 'source_kind': 'author',
                          'published': True, 'in_catalog': True, 'archived_at': None}
                if task is None:
                    task = Task(source_system=SYSTEM, external_id=row['id'], **fields)
                    created += 1
                else:
                    for key, value in fields.items():
                        setattr(task, key, value)
                    updated += 1
                task.save()
                TaskBlock.all_objects.filter(task=task).delete()
                if row.get('image'):
                    name = f'imports/physics/{row["image"]}'
                    data = (DATA / 'img' / row['image']).read_bytes()
                    if default_storage.exists(name):
                        default_storage.delete(name)
                    default_storage.save(name, ContentFile(data))
                    TaskBlock.objects.create(task=task, kind='image', position=1, image=name, text=f'Рисунок к задаче {row["number"]}.{row["id"] % 1000}')
                by_number.setdefault(row['number'], []).append(task)
            notes = self.notes(owner) if owner else 0
            collections = self.collections(by_number)
            variants = self.variants(owner, by_number) if owner else 0
        self.stdout.write(self.style.SUCCESS(f'Physics: {len(topics)} numbers, tasks created {created}, updated {updated}; '
                                             f'{notes} notes, {collections} collections, {variants} variants.'))

    def notes(self, owner):
        count = 0
        # Imported last-to-first: the library lists the newest first, so «как устроен экзамен» comes on top.
        for path in sorted((DATA / 'notes').glob('*.md'), reverse=True):
            text = path.read_text(encoding='utf-8')
            title = text.splitlines()[0].lstrip('# ').strip()
            notebook = Notebook.objects.filter(title=title).first() or Notebook(owner=owner, title=title)
            notebook.description = 'Конспект Инфокода для подготовки к ЕГЭ по физике.'
            notebook.published = True
            notebook._cell_count = 1
            notebook.save()
            notebook.cells.all().delete()
            parts = [p for p in re.split(r'(?m)^(?=## )', '\n'.join(text.splitlines()[1:])) if p.strip()]
            NotebookCell.objects.bulk_create([NotebookCell(notebook=notebook, kind='text', position=i, text=to_html(part)) for i, part in enumerate(parts, 1)])
            count += 1
        return count

    def collections(self, by_number):
        count, keys = 0, set()
        for number, tasks in sorted(by_number.items()):
            for order, (level, name, description) in enumerate(LEVELS, 1):
                picked = [t for t in tasks if t.difficulty == level]
                if not picked:
                    continue
                key = f'phys:{number}:{level}'
                collection, _ = Collection.objects.update_or_create(key=key, defaults={
                    'source': 'infocode', 'number': number, 'title': f'№ {number} — {name}', 'level': name,
                    'description': description, 'url': '', 'sort_order': order, 'published': True})
                collection.items.all().delete()
                CollectionItem.objects.bulk_create([CollectionItem(collection=collection, task=t, position=i) for i, t in enumerate(picked, 1)])
                keys.add(key)
                count += 1
        Collection.objects.filter(key__startswith='phys:').exclude(key__in=keys).delete()
        return count

    def variants(self, owner, by_number):
        made = 0
        for k in range(1, VARIANTS + 1):
            title = f'Тренировочный вариант № {k} (физика, Инфокод)'
            variant = Variant.objects.filter(title=title).first()
            if variant and ExamAttempt.objects.filter(variant=variant).exists():
                continue  # pupils already wrote it: keep as is
            if variant:
                variant.delete()
            variant = Variant.objects.create(title=title, creator=owner, kind='exam', duration=235, published=True, scoring='ege')
            picked = [tasks[(k - 1) % len(tasks)] for number, tasks in sorted(by_number.items())]
            variant.topics.set({t.topic_id for t in picked})
            VariantItem.objects.bulk_create([VariantItem(variant=variant, task=t, position=i, number=t.topic.number, statement=t.statement, answer=t.answer,
                                                         code_mode='off', solution=t.solution, blocks=[b.snapshot() for b in t.content_blocks.all()])
                                             for i, t in enumerate(picked, 1)])
            made += 1
        return made
