"""Refresh the collections section: Yandex links and the platform's own sets (repeatable)."""
from django.core.management.base import BaseCommand
from learning.collections_builder import sync_all


class Command(BaseCommand):
    help = 'Sync Yandex Uchebnik collection links and rebuild own collections from the catalogue.'

    def add_arguments(self, parser):
        parser.add_argument('--size', type=int, default=10, help='Tasks per own collection')

    def handle(self, size=10, **options):
        links, own = sync_all(size)
        self.stdout.write(self.style.SUCCESS(f'Collections: {links} Yandex links, {own} own collections.'))
