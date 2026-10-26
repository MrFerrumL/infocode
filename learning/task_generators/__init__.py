"""Original tasks made by program: every generator describes one kind of ЕГЭ task.

A generator picks parameters at random, words the task, and solves it twice — the main way and an independent
check. A task is kept only when both answers agree, the answer is in a sensible range and the same task (same
parameters or the same wording) is not in the bank yet. Tasks go to the catalogue as source_system "infocode-gen".
"""
import hashlib
import json
import random
from dataclasses import dataclass, field

SYSTEM = 'infocode-gen'
SOURCE = 'Инфокод · генератор задач'
LABEL = 'Авторская · Инфокод'


@dataclass
class Generated:
    number: int
    kind: str           # generator slug, e.g. "13.paths"
    params: dict
    title: str
    statement: str
    answer: str
    solution: str
    difficulty: str     # easy / medium / hard / ege / grave
    subtopic: str = ''
    tags: list = field(default_factory=list)

    @property
    def key(self):
        """Stable identity of the task: generator + parameters."""
        return f'{self.kind}:' + json.dumps(self.params, sort_keys=True, ensure_ascii=False)

    @property
    def external_id(self):
        # Fits the 32-bit integer column; a rare clash is caught by the wording check.
        return int(hashlib.sha256(self.key.encode()).hexdigest()[:7], 16)


class Rejected(Exception):
    """These parameters do not make a good task (answer too small, too big, or ambiguous)."""


class Generator:
    number = 0
    slug = ''
    title = ''
    subtopic = ''

    def params(self, rng):
        """Random parameters of one task."""
        raise NotImplementedError

    def solve(self, p):
        """The answer, the main way."""
        raise NotImplementedError

    def check(self, p):
        """The answer found independently (another algorithm), to catch mistakes in either."""
        raise NotImplementedError

    def word(self, p, answer):
        """(statement, solution, difficulty) in the generator's own words."""
        raise NotImplementedError

    def make(self, rng):
        p = self.params(rng)
        answer = self.solve(p)
        if answer != self.check(p):
            raise AssertionError(f'{self.slug}: two methods disagree for {p}: {answer} vs {self.check(p)}')
        statement, solution, difficulty = self.word(p, answer)
        return Generated(self.number, self.slug, p, self.title, statement, str(answer), solution, difficulty, self.subtopic)


REGISTRY = {}


def register(cls):
    REGISTRY.setdefault(cls.number, []).append(cls())
    return cls


def generate(number, count, seed=None, taken=(), tries=50):
    """Up to `count` new tasks of a number; `taken` holds keys, wordings and external ids already in the bank."""
    rng = random.Random(seed)
    seen = set(taken)
    made = []
    generators = REGISTRY.get(number, [])
    share = {g.slug: 0 for g in generators}  # every kind of task gets an even share
    attempts = 0
    while generators and len(made) < count and attempts < count * tries:
        attempts += 1
        generator = min(generators, key=lambda g: (share[g.slug], rng.random()))
        try:
            task = generator.make(rng)
        except Rejected:
            continue
        wording = normal(task.statement)
        if task.key in seen or wording in seen or task.external_id in seen:
            continue
        seen.update((task.key, wording))
        share[task.kind] += 1
        made.append(task)
    return made


def normal(text):
    return ' '.join(text.lower().split())


def taken_in(topic):
    """Wordings and generator ids already in the bank for this number: new tasks must differ from all of them."""
    from learning.models import Task
    bank = Task.all_objects.filter(topic=topic)
    taken = {normal(text) for text in bank.exclude(statement='').values_list('statement', flat=True)}
    return taken | set(bank.filter(source_system=SYSTEM).values_list('external_id', flat=True))


def save(tasks, topic, published=True, in_catalog=True):
    """Store generated tasks in the bank; returns the saved Task objects in the same order."""
    from learning.models import Task
    rows = [Task(topic=topic, title=f'Инфокод · № {topic.number} · {task.title}', statement=task.statement, answer=task.answer,
                 solution=task.solution, difficulty=task.difficulty, source=SOURCE, source_system=SYSTEM,
                 external_id=task.external_id, source_label=LABEL, source_kind='author', subtopic=task.subtopic,
                 tags='генератор', published=published, in_catalog=in_catalog) for task in tasks]
    Task.all_objects.bulk_create(rows)
    return list(Task.all_objects.filter(topic=topic, source_system=SYSTEM, external_id__in=[t.external_id for t in tasks]))


from . import n13  # noqa: E402,F401  (generators register themselves)
