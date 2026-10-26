from io import BytesIO
from pathlib import PurePosixPath
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from PIL import Image
from learning.condition_labels import clean, plan
from learning.models import TaskBlock

SUFFIX = '-clean'


class Command(BaseCommand):
    help = ("Erase K. Polyakov's task number (\"270)\") from the first condition picture of each of his tasks and cut off "
            "the tail of the previous task above it. The number stays in the database for sorting; the original picture stays on disk "
            "next to the cleaned one (--revert puts the originals back).")

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--limit', type=int, default=0)
        parser.add_argument('--revert', action='store_true')

    def first_images(self):
        seen = set()
        blocks = (TaskBlock.all_objects.filter(task__source_system='polyakov', kind='image', purpose='statement')
                  .order_by('task_id', 'position', 'id').only('id', 'task_id', 'image'))
        for block in blocks.iterator():
            if block.task_id not in seen:
                seen.add(block.task_id)
                yield block

    def handle(self, *args, **opts):
        if opts['revert']:
            return self.revert()
        counts = {'cleaned': 0, 'already': 0, 'no number': 0, 'missing': 0, 'tail above': 0}
        for n, block in enumerate(self.first_images()):
            if opts['limit'] and n >= opts['limit']:
                break
            name = block.image.name
            if PurePosixPath(name).stem.endswith(SUFFIX):
                counts['already'] += 1
                continue
            if not name or not default_storage.exists(name):
                counts['missing'] += 1
                continue
            with default_storage.open(name, 'rb') as source:
                image = Image.open(source)
                image.load()
            found = plan(image)
            result = clean(image) if found else None
            if result is None:
                counts['no number'] += 1
                continue
            counts['tail above'] += bool(found.above)
            counts['cleaned'] += 1
            if opts['dry_run']:
                continue
            buffer = BytesIO()
            result.save(buffer, 'JPEG', quality=92, optimize=True)
            path = PurePosixPath(name)
            saved = default_storage.save(str(path.with_name(f'{path.stem}{SUFFIX}.jpg')), ContentFile(buffer.getvalue()))
            TaskBlock.all_objects.filter(pk=block.pk).update(image=saved)
        self.stdout.write(', '.join(f'{k}: {v}' for k, v in counts.items()) + ('' if not opts['dry_run'] else ' (dry run, nothing saved)'))

    def revert(self):
        restored = 0
        for block in self.first_images():
            path = PurePosixPath(block.image.name)
            if path.stem.endswith(SUFFIX):
                originals = [str(path.with_name(path.stem[:-len(SUFFIX)] + ext)) for ext in ('.jpg', '.png', '.jpeg', '.webp')]
                original = next((o for o in originals if default_storage.exists(o)), None)
                if original:
                    TaskBlock.all_objects.filter(pk=block.pk).update(image=original)
                    restored += 1
        self.stdout.write(f'Original pictures restored: {restored}.')
