"""Prepare an empty catalogue without adding demonstration content or accounts."""
from django.core.management.base import BaseCommand
from django.db import transaction

from learning.models import Topic
from learning.topic_defaults import TOPICS


class Command(BaseCommand):
    help = 'Create missing catalogue headings 1–27 without demo tasks or accounts. Preserve existing headings.'

    @transaction.atomic
    def handle(self, *args, **options):
        created_count = 0
        for number, title in enumerate(TOPICS, start=1):
            _, created = Topic.objects.get_or_create(number=number, defaults={'title': title})
            created_count += int(created)
        self.stdout.write(self.style.SUCCESS(
            f'Catalogue ready: 27 headings; created {created_count}, already present {27 - created_count}. '
            'No tasks, courses or accounts were added.'
        ))
