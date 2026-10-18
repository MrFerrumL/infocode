"""Load the neural similar-task neighbours (learning/data/similar_tasks.json.gz) into the database."""
from django.core.management.base import BaseCommand
from learning.similar_tasks import DATA, sync


class Command(BaseCommand):
    help = 'Load similar tasks for the "Похожие" button.'

    def add_arguments(self, parser):
        parser.add_argument('--path', default=str(DATA))

    def handle(self, path, **options):
        tasks, links = sync(path)
        self.stdout.write(self.style.SUCCESS(f'Similar tasks: {tasks} tasks, {links} links.'))
