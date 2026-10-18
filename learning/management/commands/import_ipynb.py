"""Import a Colab notebook (.ipynb) into «Конспекты»; a notebook of the same owner and title is updated in place."""
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from learning.ipynb_import import import_notebook, read, title_of
from learning.notebook_models import Notebook


class Command(BaseCommand):
    help = 'Import a Colab / Jupyter notebook into «Конспекты».'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--title', default='')
        parser.add_argument('--owner', help='Teacher username (default: the first administrator)')
        parser.add_argument('--publish', action='store_true')

    def handle(self, path, title='', owner=None, publish=False, **options):
        users = get_user_model().objects
        user = users.filter(username=owner).first() if owner else users.filter(is_superuser=True, is_active=True).order_by('pk').first()
        if user is None:
            raise CommandError('Teacher account not found.')
        data = Path(path).read_bytes()
        try:
            name = title or title_of(read(data))
            existing = Notebook.objects.filter(owner=user, title=name).first() if name else None
            notebook, count = import_notebook(data, user, title=title, notebook=existing, filename=Path(path).name, publish=publish or None)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        state = 'updated' if existing else 'created'
        self.stdout.write(self.style.SUCCESS(f'Notebook #{notebook.pk} «{notebook.title}»: {count} cells ({state}).'))
