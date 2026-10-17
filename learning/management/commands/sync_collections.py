"""Rebuild the platform's collections (named like Yandex Uchebnik's) from the catalogue (repeatable)."""
from django.core.management.base import BaseCommand
from learning.collections_builder import sync_all


class Command(BaseCommand):
    help = 'Rebuild collections named like Yandex Uchebnik ones from the KOMPEGE and Polyakov catalogue.'

    def add_arguments(self, parser):
        parser.add_argument('--size', type=int, default=10, help='Tasks per own collection')

    def handle(self, size=10, **options):
        removed, own = sync_all(size)
        self.stdout.write(self.style.SUCCESS(f'Collections: {own} built; {removed} old Yandex links removed.'))
