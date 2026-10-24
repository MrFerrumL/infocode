"""Build the informatics course «ЕГЭ по информатике · №1–27» from learning/data/course_inf: one section per number,
theory written for the platform, self-check steps and practice taken from the task bank (easy → exam level → harder).

Each NN.txt file is one section. Header lines (`title:`, `summary:`, `lesson:`, `minutes:`) are followed by slides:
  === theory: Title    HTML; `--- code` … `--- end` inserts a Python block
  === quiz: Title      `? question`, `+ right option`, `- wrong option`, `>> explanation`
  === answer: Title    HTML condition, `= answer`, `>> explanation`
  === bank: Title      `slot: step title | difficulty | source kinds in order of preference`
Safe to run again: sections, lessons and steps are matched by position and title, a chosen bank task is kept while it
stays published, and a step's revision changes only when its graded content changes, so pupils keep their progress.
"""
import re
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F, Q
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
            title, level, kinds = [part.strip() for part in line[5:].split('|')]
            slide['slots'].append({'title': title, 'level': level, 'kinds': kinds.split()})
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
                    specs = [dict(slide, title=s['title'], slot=s) for s in slide['slots']] if slide['kind'] == 'bank' else [slide]
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
            step.task = self.pick(lesson, step, spec['slot'])
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
            return step.task
        number = lesson.section.order
        base = (Task.objects.filter(topic__number=number, published=True, in_catalog=True)
                .exclude(answer='').exclude(pk__in=self.used)
                .order_by(F('external_id').desc(nulls_last=True), '-id'))   # newest source tasks first, as in the catalogue
        for query in [*(Q(difficulty=slot['level'], source_kind=kind) for kind in slot['kinds']),
                      Q(difficulty=slot['level']), Q(source_kind__in=slot['kinds']), Q()]:
            task = base.filter(query).first()
            if task:
                self.used.add(task.pk)
                return task
        return None
