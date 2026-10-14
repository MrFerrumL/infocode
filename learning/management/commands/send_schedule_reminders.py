from django.core.management.base import BaseCommand
from learning.schedule_services import send_reminders


class Command(BaseCommand):
    help = 'Deliver upcoming lesson reminders inside the platform, once per occurrence and user.'

    def handle(self, **options):
        count = send_reminders()
        if options.get('verbosity', 1):
            self.stdout.write(f'Schedule reminders: {count}')
