"""Import Yandex Uchebnik collections saved by the teacher (see learning/yandex_import.py for the layout)."""
from django.core.management.base import BaseCommand, CommandError
from learning.yandex_import import import_collections, plan, read_source


class Command(BaseCommand):
    help = 'Import hand-saved Yandex Uchebnik collections (folder or zip): condition pictures, files and ответы.txt.'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--dry-run', action='store_true', help='Only check the folder; nothing is saved.')

    def handle(self, path, dry_run=False, **options):
        try:
            if dry_run:
                found = plan(read_source(path))
                self.stdout.write(self.style.SUCCESS(f'Checked {len(found)} collections, {sum(len(rows) for *_, rows in found)} tasks. Dry run: nothing saved.'))
                return
            collections, created, updated = import_collections(path)
        except (OSError, ValueError) as exc:
            raise CommandError('Yandex import failed:\n' + str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f'Yandex collections: {collections} imported; tasks created {created}, updated {updated}.'))
