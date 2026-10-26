"""Build the informatics course «ЕГЭ по информатике · №1–27» from learning/data/course_inf: one section per number,
theory written for the platform, self-check steps and practice taken from the task bank (easy → exam level → harder).

Each NN.txt file is one section. Header lines (`title:`, `summary:`, `lesson:`, `minutes:`) are followed by slides:
  === theory: Title    HTML; `--- code` … `--- end` inserts a Python block
  === quiz: Title      `? question`, `+ right option`, `- wrong option`, `>> explanation`
  === answer: Title    HTML condition, `= answer`, `>> explanation`
  === bank: Title      `slot: step title | difficulty | source kinds in order of preference [| group]`
                       (group: one 19–21 game as three slides — questions 19, 20, 21)
Safe to run again: sections, lessons and steps are matched by position and title, a chosen bank task is kept while it
stays published, and a step's revision changes only when its graded content changes, so pupils keep their progress.
"""
import re
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q
from learning import game_groups
from learning.models import Course, Lesson, LessonProgress, Section, Step, StepContentBlock, Task
from learning.rich_text import sanitize_html
from learning.subjects import using

DATA = Path(__file__).resolve().parents[2] / 'data' / 'course_inf'
TITLE = 'ЕГЭ по информатике · №1–27'
DESCRIPTION = ('Курс по всем номерам ЕГЭ по информатике: теория своими словами, примеры на Python, вопросы на понимание '
               'и задачи из банка Инфокода — от разминки до уровня экзамена. Без видео: читайте, решайте, проверяйте себя.')


def parse(path):
    """One section file → header dict and a list of slides."""
    head, slides, slide, code = {}, [], None, None
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.rstrip()
        if code is not None:
            if line == '--- end':
                slide['blocks'].append(('code', '\n'.join(code).strip('\n')))
                code = None
            else:
                code.append(raw)
            continue
        found = re.match(r'^=== (theory|quiz|answer|bank): (.+)$', line)
        if found:
            slide = {'kind': found[1], 'title': found[2].strip(), 'blocks': [], 'options': [], 'right': [],
                     'answer': '', 'explanation': '', 'slots': []}
            slides.append(slide)
            continue
        if slide is None:
            if ':' in line:
                key, value = line.split(':', 1)
                head[key.strip()] = value.strip()
            continue
        if line == '--- code':
            code = []
        elif slide['kind'] == 'bank' and line.startswith('slot:'):
            title, level, kinds, *flags = [part.strip() for part in line[5:].split('|')]
            slide['slots'].append({'title': title, 'level': level, 'kinds': kinds.split(), 'group': 'group' in flags})
        elif line.startswith('>> '):
            slide['explanation'] = line[3:].strip()
        elif slide['kind'] == 'quiz' and line[:2] in ('? ', '+ ', '- '):
            if line.startswith('? '):
                slide['blocks'].append(('text', f'<p>{line[2:].strip()}</p>'))
            else:
                if line.startswith('+ '):
                    slide['right'].append(len(slide['options']))
                slide['options'].append(line[2:].strip())
        elif slide['kind'] == 'answer' and line.startswith('= '):
            slide['answer'] = line[2:].strip()
        elif line.strip():
            if slide['blocks'] and slide['blocks'][-1][0] == 'text':
                slide['blocks'][-1] = ('text', slide['blocks'][-1][1] + '\n' + line)
            else:
                slide['blocks'].append(('text', line))
    for slide in slides:
        slide['blocks'] = [(kind, sanitize_html(text) if kind == 'text' else text) for kind, text in slide['blocks']]
    for key in ('title', 'lesson'):
        if not head.get(key):
            raise CommandError(f'{path.name}: нет строки «{key}:».')
    return head, slides


class Command(BaseCommand):
    help = 'Create or update the informatics course №1–27 (theory, self-checks, practice from the bank).'

    def add_arguments(self, parser):
        parser.add_argument('--publish', action='store_true', help='Опубликовать курс для учеников.')

    def handle(self, **options):
        owner = get_user_model().objects.filter(is_superuser=True, is_active=True).order_by('pk').first()
        if not owner:
            raise CommandError('Нужен активный администратор — он станет автором курса.')
        files = sorted(DATA.glob('[0-9][0-9].txt'))
        with using('inf'), transaction.atomic():
            course = Course.objects.filter(title=TITLE).first() or Course(title=TITLE, owner=owner)
            course.description = DESCRIPTION
            course.published = course.published or options['publish']
            course.save()
            self.used = set(Step.objects.filter(lesson__section__course=course, kind='task')
                            .exclude(task=None).values_list('task_id', flat=True))
            self.games = {}
            # The same exam task may sit in two banks (KOMPEGE and K. Polyakov): one answer per number is enough.
            self.answers = set()
            stats = {'steps': 0, 'revised': 0, 'tasks': 0}
            for path in files:
                order = int(path.stem)
                head, slides = parse(path)
                section = Section.objects.filter(course=course, order=order).first() or Section(course=course, order=order)
                section.title, section.description = head['title'], head.get('summary', '')
                section.save()
                lesson = section.lessons.order_by('order', 'id').first() or Lesson(section=section, order=1)
                lesson.title, lesson.minutes = head['lesson'], int(head.get('minutes', 20))
                lesson.theory, lesson.published = head.get('summary', ''), True
                lesson.save()
                position = 0
                for slide in slides:
                    if slide['kind'] != 'bank':
                        specs = [slide]
                    else:   # a 19–21 game is three slides in a row: questions 19, 20 and 21 of one condition
                        specs = [dict(slide, title=f'{s["title"]} · задание {n}' if s['group'] else s['title'], slot=s,
                                      part=n if s['group'] else None)
                                 for s in slide['slots'] for n in (game_groups.NUMBERS if s['group'] else (None,))]
                    for spec in specs:
                        position += 1
                        self.step(lesson, spec, position, stats)
            self.stdout.write(self.style.SUCCESS(
                f'Курс «{TITLE}»: разделов {len(files)}, шагов {stats["steps"]}, задач из банка {stats["tasks"]}, '
                f'обновлено с новой редакцией {stats["revised"]}. {"Опубликован" if course.published else "Черновик — опубликуйте в редакторе курса или запустите с --publish"}.'))

    def step(self, lesson, spec, position, stats):
        step = Step.all_objects.filter(lesson=lesson, title=spec['title']).first()
        new = step is None
        step = step or Step(lesson=lesson, title=spec['title'])
        before = None if new else (step.kind, step.answer, step.options, step.correct_choices, step.task_id, step.code_mode,
                                   list(step.content_blocks.values_list('kind', 'text')))
        step.position, step.published, step.archived_at = position, True, None
        if spec['kind'] == 'bank':
            step.kind, step.use_blocks, step.points = 'task', False, 1
            step.task = self.pick_part(lesson, step, spec) if spec.get('part') else self.pick(lesson, step, spec['slot'])
            step.code_mode = step.task.code_mode if step.task else 'off'
            step.published = step.task is not None
            stats['tasks'] += step.task is not None
        else:
            step.kind = {'theory': 'text', 'quiz': 'quiz', 'answer': 'answer'}[spec['kind']]
            step.use_blocks, step.task = True, None
            step.points = 0 if spec['kind'] == 'theory' else 1
            step.options, step.correct_choices = spec['options'], spec['right']
            step.quiz_mode = 'single' if len(spec['right']) == 1 else 'auto'
            step.answer, step.explanation = spec['answer'], spec['explanation']
        after = (step.kind, step.answer, step.options, step.correct_choices, step.task_id, step.code_mode,
                 [(kind, text) for kind, text in spec['blocks']] if step.use_blocks else [])
        if before is not None and before != after:
            step.revision += 1
            stats['revised'] += 1
            LessonProgress.objects.filter(lesson=lesson).update(passed=False)
        step._content_kinds = [kind for kind, _ in spec['blocks']]
        step._content_notebooks = []
        step.full_clean(exclude=['lesson'])
        step.save()
        if step.use_blocks and (new or before[-1] != after[-1]):
            step.content_blocks.all().delete()
            StepContentBlock.objects.bulk_create([StepContentBlock(step=step, kind=kind, text=text, position=n)
                                                  for n, (kind, text) in enumerate(spec['blocks'], 1)])
        stats['steps'] += 1

    def pick(self, lesson, step, slot):
        """Keep the chosen task while it is published; otherwise take an unused one: wanted level and source first."""
        if step.task_id and step.task.published and step.task.in_catalog and not step.task.archived_at:
            answer = (step.task.topic.number, ' '.join(step.task.answer.split()))
            if answer not in self.answers:           # an earlier slide may already hold the same task from another bank
                self.answers.add(answer)
                return step.task
        number = lesson.section.order
        base = (Task.objects.filter(topic__number=number, published=True, in_catalog=True)
                .exclude(answer='').exclude(pk__in=self.used)
                .order_by(F('external_id').desc(nulls_last=True), '-id'))   # newest source tasks first, as in the catalogue
        return self.first(base, slot)

    def first(self, base, slot):
        for query in [*(Q(difficulty=slot['level'], source_kind=kind) for kind in slot['kinds']),
                      Q(difficulty=slot['level']), Q(source_kind__in=slot['kinds']), Q()]:
            for task in base.filter(query).select_related('topic')[:50]:
                answer = (task.topic.number, ' '.join(task.answer.split()))
                if answer not in self.answers:
                    self.used.add(task.pk)
                    self.answers.add(answer)
                    return task
        return None

    def pick_part(self, lesson, step, spec):
        """Questions of one 19–21 game: slide 19 chooses the game (kept while it stays complete), 20 and 21 follow it."""
        key = (lesson.pk, spec['slot']['title'])
        if spec['part'] == game_groups.FIRST:
            task = step.task if step.task_id and self.complete(step.task) else None
            if not task:
                partner = lambda n: Task.objects.filter(topic__number=n, published=True, in_catalog=True, source_system=OuterRef('source_system'),
                                                       external_id=OuterRef('external_id')).exclude(answer='')
                games = (Task.objects.filter(topic__number=game_groups.FIRST, published=True, in_catalog=True, external_id__isnull=False)
                         .exclude(answer='').exclude(source_system='').exclude(pk__in=self.used)
                         .filter(*(Exists(partner(n)) for n in game_groups.FOLLOW_UPS))
                         .order_by(F('external_id').desc(nulls_last=True), '-id'))
                task = self.first(games, spec['slot'])
            self.games[key] = task
            if task:
                self.used.add(task.pk)
            return task
        game = self.games.get(key)
        if not game:
            return None
        task = (Task.objects.filter(topic__number=spec['part'], published=True, in_catalog=True, source_system=game.source_system,
                                    external_id=game.external_id).exclude(answer='').order_by('id').first())
        if task:
            self.used.add(task.pk)
        return task

    @staticmethod
    def complete(task):
        """A 19 task that is still published and has published 20 and 21 with answers."""
        return (task.published and task.in_catalog and not task.archived_at and task.topic.number == game_groups.FIRST
                and all(Task.objects.filter(topic__number=n, published=True, in_catalog=True, source_system=task.source_system,
                                            external_id=task.external_id).exclude(answer='').exists() for n in game_groups.FOLLOW_UPS))
