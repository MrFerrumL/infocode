from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Q
from learning.models import Task, Topic
from learning.subjects import using
from learning.task_generators import REGISTRY, generate, save, taken_in


class Command(BaseCommand):
    help = ('Add original generated informatics tasks (learning/task_generators) to the bank: answers found twice, '
            'no repeats of tasks already in the bank. Without --publish the tasks stay drafts for the teacher to review.')

    def add_arguments(self, parser):
        parser.add_argument('--numbers', nargs='*', type=int, default=[], help='ЕГЭ numbers, e.g. 23')
        parser.add_argument('--thinnest', type=int, default=0, help='Take this many numbers with the fewest catalogue tasks')
        parser.add_argument('--count', type=int, default=20, help='New tasks per number')
        parser.add_argument('--seed', type=int, default=None)
        parser.add_argument('--publish', action='store_true')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        with using('inf'):
            numbers = list(opts['numbers'])
            if opts['thinnest']:
                counts = dict(Topic.objects.filter(number__in=REGISTRY).annotate(
                    n=Count('tasks', filter=Q(tasks__published=True, tasks__in_catalog=True))).values_list('number', 'n'))
                numbers += sorted(REGISTRY, key=lambda n: counts.get(n, 0))[:opts['thinnest']]
            missing = [n for n in numbers if n not in REGISTRY]
            if missing or not numbers:
                raise CommandError(f'Generators exist for numbers {sorted(REGISTRY)}; asked: {numbers or "nothing"}.')
            for number in dict.fromkeys(numbers):
                self.fill(number, opts)

    def fill(self, number, opts):
        topic = Topic.objects.get(number=number)
        bank = Task.all_objects.filter(topic=topic)
        tasks = generate(number, opts['count'], seed=opts['seed'], taken=taken_in(topic))
        levels = {}
        for task in tasks:
            levels[task.difficulty] = levels.get(task.difficulty, 0) + 1
        self.stdout.write(f'№ {number}: {len(tasks)} new tasks ({", ".join(f"{k} {v}" for k, v in sorted(levels.items()))}); '
                          f'already in the bank: {bank.count()}.')
        if opts['dry_run']:
            for task in tasks[:3]:
                self.stdout.write(f'--- {task.kind}, {task.difficulty}, answer {task.answer}\n{task.statement}\n')
            return
        with transaction.atomic():
            save(tasks, topic, published=opts['publish'])
        state = 'published' if opts['publish'] else 'saved as drafts (publish them in the bank editor or rerun with --publish)'
        self.stdout.write(self.style.SUCCESS(f'№ {number}: {len(tasks)} tasks {state}.'))
