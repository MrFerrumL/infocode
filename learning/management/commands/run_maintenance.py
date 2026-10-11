import time
from pathlib import Path
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = 'Finish timed-out attempts every minute; create and verify one daily backup.'

    def add_arguments(self, parser):
        parser.add_argument('--backup-dir', required=True)
        parser.add_argument('--keep', type=int, default=3)
        parser.add_argument('--once', action='store_true')

    def cycle(self, folder, keep, last_backup):
        call_command('expire_attempts', verbosity=0)
        today = timezone.localdate().isoformat()
        if last_backup == today:
            return last_backup
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'infocode-{today}.zip'
        call_command('backup_platform', str(path), verify=path.exists())
        # Retention runs only after today's archive has been verified successfully.
        archives = sorted((p for p in folder.glob('infocode-????-??-??.zip') if p.is_file() and not p.is_symlink()), reverse=True)
        for old in archives[keep:]:
            old.unlink()
        return today

    def handle(self, backup_dir, keep, once, **options):
        if keep < 1:
            raise CommandError('--keep must be at least 1')
        folder = Path(backup_dir).resolve()
        last_backup = None
        while True:
            try:
                last_backup = self.cycle(folder, keep, last_backup)
            except Exception as exc:
                if once:
                    raise CommandError(str(exc)) from exc
                self.stderr.write(f'Maintenance failed ({type(exc).__name__}); retry in 60 seconds.')
            if once:
                break
            time.sleep(60)
