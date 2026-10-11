from django.core.management.base import BaseCommand
from learning.course_services import bootstrap_course

class Command(BaseCommand):
    help = 'Convert legacy course content to modules, lessons and steps without overwriting edits.'
    def handle(self, *args, **options):
        bootstrap_course()
        self.stdout.write('Legacy content upgraded.')
