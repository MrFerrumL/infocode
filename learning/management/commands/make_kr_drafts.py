import json
import random
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from learning.exam_sources import OFFICIAL
from learning.kr_api import ComposeError, by_source, pool
from learning.kr_views import NUMBERS, compose_draft
from learning.models import KrDraft, Task
from learning.subjects import using


class Command(BaseCommand):
    help = ('Build КР drafts for a teacher from a JSON plan: [{"title": "…", "numbers": {"5": 3, "17": 4}, "variants": 4, '
            '"duration": 45, "source": "official"}]. "numbers" is how many tasks of each number go into ONE variant. '
            'Tasks never repeat between the drafts of one run. A draft whose title already exists is left alone '
            '(the teacher may have edited it) unless --replace is given. The teacher edits the drafts on the КР page.')

    def add_arguments(self, parser):
        parser.add_argument('--user', required=True, help='username of the teacher (staff)')
        parser.add_argument('--plan', type=Path, required=True)
        parser.add_argument('--subject', default='inf')
        parser.add_argument('--replace', action='store_true', help='rebuild drafts whose title already exists')
        parser.add_argument('--dry-run', action='store_true')

    @staticmethod
    def share_source(subject, todo, used):
        """Per draft, the tasks of its source that stay for it alone: for every number the tasks of that source (not used
        yet) are dealt out evenly between the drafts that take the number, so the first draft does not use them all up."""
        reserved = [set() for _ in todo]
        rng = random.Random(len(todo))
        with using(subject):
            numbers = {int(n) for item in todo for n in item['numbers']}
            for number in sorted(numbers):
                takers = [k for k, item in enumerate(todo) if str(number) in item['numbers'] and item.get('source', 'official')]
                if len(takers) < 2:
                    continue
                for source in {todo[k].get('source', 'official') for k in takers}:
                    group = [k for k in takers if todo[k].get('source', 'official') == source]
                    ids = list(by_source(pool().filter(topic__number=number).exclude(pk__in=used), source).values_list('pk', flat=True))
                    rng.shuffle(ids)
                    share = len(ids) // max(len(group), 1)
                    for turn, k in enumerate(group):
                        reserved[k] |= set(ids[turn * share:(turn + 1) * share])
        return reserved

    def handle(self, *args, **opts):
        user = get_user_model().objects.filter(username=opts['user'], is_staff=True, is_active=True).first()
        if not user:
            raise CommandError(f'No active teacher «{opts["user"]}».')
        subject = opts['subject']
        if subject not in NUMBERS:
            raise CommandError('Unknown subject.')
        plan = json.loads(opts['plan'].read_text(encoding='utf-8'))
        used, made = set(), 0
        todo = []
        for item in plan:
            existing = KrDraft.objects.filter(owner=user, title=item['title'][:160])
            if existing.exists() and not opts['replace']:
                self.stdout.write(f'«{item["title"][:160]}»: already there, left as it is.')
                used |= {i for row in existing for ids in row.data.get('variants', []) for i in ids}
            else:
                todo.append(item)
        reserved = self.share_source(subject, todo, used)  # the scarce tasks of the source are shared evenly between the drafts
        for position, item in enumerate(todo):
            title = item['title'][:160]
            existing = KrDraft.objects.filter(owner=user, title=title)
            rows = [{'number': int(n), 'count': int(c), 'source': 'bank', 'ids': []} for n, c in item['numbers'].items()]
            variants = int(item.get('variants', 4))
            source = item.get('source', 'official')
            try:  # a number with too few tasks of the source gets the rest from the whole bank
                others = {i for k, ids in enumerate(reserved) if k != position for i in ids}
                result = compose_draft(subject, rows, variants, item.get('level', ''), source, item.get('year', ''),
                                       exclude=used | others, fill=True)
            except ComposeError as exc:
                raise CommandError(f'«{title}»: {exc}')
            chosen = {i for ids in result for i in ids}
            used |= chosen
            note = ''
            if source:
                from_source = Task.all_objects.filter(pk__in=chosen, exam_source__in=OFFICIAL if source == 'official' else [source]).count()
                if from_source < len(chosen):
                    note = f' ({len(chosen) - from_source} of {len(chosen)} are not from the source «{source}»: too few of them left)'
            draft = {'subject': subject, 'title': title, 'group': item.get('group', ''), 'duration': int(item.get('duration', 45)),
                     'level': item.get('level', ''), 'source': source, 'year': item.get('year', ''), 'variants': result}
            if not opts['dry_run']:
                if opts['replace']:
                    existing.delete()
                KrDraft.objects.create(owner=user, title=title, data=draft)
            made += 1
            self.stdout.write(f'«{title}»: {variants} variants, {[len(ids) for ids in result]} tasks{note}')
        self.stdout.write(f'Drafts made: {made}' + (' (dry run, nothing saved)' if opts['dry_run'] else '') + '.')
