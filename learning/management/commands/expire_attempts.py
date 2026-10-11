from django.core.management.base import BaseCommand
from learning.services import expire_attempts

class Command(BaseCommand):
    help = 'Finish expired attempts using saved answers; safe to schedule every minute.'
    def handle(self, *args, **options):
        self.stdout.write(f'Completed: {expire_attempts()}')
