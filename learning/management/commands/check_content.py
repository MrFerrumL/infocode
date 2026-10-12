from django.core.management.base import BaseCommand, CommandError
from learning.readiness import content_report


class Command(BaseCommand):
    help = 'Check migrations, published content and referenced media; does not change data.'

    def add_arguments(self, parser):
        parser.add_argument('--strict', action='store_true', help='Exit with an error if database/migrations/files need repair.')

    def handle(self, strict, **options):
        report = content_report()
        for item in report['checks']:
            self.stdout.write(f"[{item['level'].upper()}] {item['title']}: {item['detail']}")
        for item in report['missing']:
            self.stdout.write(f"Missing: {item['name']} ({item['source']})")
        if strict and report['has_errors']:
            raise CommandError('Repair the errors above before launch. This check made no changes.')
