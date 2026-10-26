"""№ 23: an executor turns one number into another; count programs or reachable results."""
from functools import lru_cache
from itertools import product
from . import Generator, Rejected, register

NAMES = ['Альфа', 'Вектор', 'Сигма', 'Кварк', 'Зенит', 'Орбита', 'Пиксель', 'Модуль', 'Лямбда', 'Квант']
ADD_WORDS = {1: 'прибавь 1', 2: 'прибавь 2', 3: 'прибавь 3', 4: 'прибавь 4'}
MUL_WORDS = {2: 'умножь на 2', 3: 'умножь на 3', 4: 'умножь на 4'}


def apply(command, value):
    kind, k = command
    return value + k if kind == 'add' else value * k


def describe(commands):
    """«1. прибавь 1; 2. умножь на 2» and the commands' own words."""
    words = [ADD_WORDS[k] if kind == 'add' else MUL_WORDS[k] for kind, k in commands]
    lines = '\n'.join(f'{i}. {w.capitalize()}' for i, w in enumerate(words, 1))
    return lines, words


def pick_commands(rng, count):
    adds = rng.sample(sorted(ADD_WORDS), k=rng.choice([1, 2]) if count == 3 else 1)
    muls = rng.sample(sorted(MUL_WORDS), k=count - len(adds))
    commands = [('add', k) for k in sorted(adds)] + [('mul', k) for k in sorted(muls)]
    return [list(c) for c in commands]


@register
class Paths(Generator):
    """How many programs turn A into B (optionally through C and/or avoiding D)."""
    number, slug, title, subtopic = 23, '23.paths', 'Сколько программ', 'Исполнитель: число программ'

    def params(self, rng):
        count = rng.choice([2, 2, 3])
        commands = pick_commands(rng, count)
        start = rng.randint(1, 12)
        target = start + (rng.randint(12, 45) if count == 2 else rng.randint(10, 26))
        through = avoid = None
        if rng.random() < .7:
            through = rng.randint(start + 3, target - 3)
        if rng.random() < .5:
            avoid = rng.randint(start + 2, target - 2)
            if avoid == through:
                avoid = None
        return {'name': rng.choice(NAMES), 'commands': commands, 'start': start, 'target': target, 'through': through, 'avoid': avoid}

    def solve(self, p):
        """Forward table: ways[v] — programs from the start to v (A→C, then C→B)."""
        def count(a, b):
            ways = [0] * (b + 1)
            ways[a] = 1
            for v in range(a, b + 1):
                if v == p['avoid'] or not ways[v]:
                    continue
                for c in p['commands']:
                    w = apply(c, v)
                    if w <= b:
                        ways[w] += ways[v]
            return ways[b]
        answer = count(p['start'], p['through']) * count(p['through'], p['target']) if p['through'] else count(p['start'], p['target'])
        if not 4 <= answer <= 2_000_000:
            raise Rejected
        return answer

    def check(self, p):
        """Recursive search from the start, remembering whether C was passed."""
        a, b, c, d = p['start'], p['target'], p['through'], p['avoid']

        @lru_cache(maxsize=None)
        def go(v, passed):
            if v > b or v == d:
                return 0
            passed = passed or v == c
            if v == b:
                return 1 if (passed or c is None) else 0
            return sum(go(apply(cmd, v), passed) for cmd in p['commands'])
        return go(a, False)

    def word(self, p, answer):
        lines, words = describe(p['commands'])
        name, a, b, c, d = p['name'], p['start'], p['target'], p['through'], p['avoid']
        rules = []
        if c:
            rules.append(f'при вычислениях встречается число {c}')
        if d:
            rules.append(f'не встречается число {d}')
        condition = f', причём {" и ".join(rules)}' if rules else ''
        statement = (f'Исполнитель {name} работает с целым числом на экране. У него {len(words)} команды:\n{lines}\n'
                     f'Каждая команда заменяет число на экране результатом своего действия. Программа — это '
                     f'последовательность команд.\n\nСколько существует программ, которые число {a} превращают в число {b}{condition}?')
        steps = ['Каждое число v можно получить только из тех чисел, из которых в v ведёт одна команда, поэтому '
                 'число программ, приводящих в v, равно сумме чисел программ для этих «предков».']
        if c:
            steps.append(f'Траектория обязана пройти через {c}: считаем программы {a} → {c} и {c} → {b} и перемножаем.')
        if d:
            steps.append(f'Число {d} запрещено: программ, проходящих через него, нет — для него ставим 0.')
        code = ['def f(a, b):', '    if a > b' + (f' or a == {d}' if d else '') + ':', '        return 0',
                '    if a == b:', '        return 1',
                '    return ' + ' + '.join(f'f(a {"+" if k == "add" else "*"} {n}, b)' for k, n in p['commands'])]
        call = f'f({a}, {c}) * f({c}, {b})' if c else f'f({a}, {b})'
        solution = '\n'.join(steps) + '\n\nТа же идея рекурсией на Python:\n' + '\n'.join(code) + f'\n\nprint({call})\n\nОтвет: {answer}'
        level = len(p['commands']) - 2 + bool(c) + bool(d)
        return statement, solution, ['easy', 'medium', 'hard', 'ege'][level]


@register
class Results(Generator):
    """How many different numbers programs of exactly N commands can give from A."""
    number, slug, title, subtopic = 23, '23.results', 'Сколько разных результатов', 'Исполнитель: множество результатов'

    def params(self, rng):
        commands = pick_commands(rng, rng.choice([2, 3]))
        return {'name': rng.choice(NAMES), 'commands': commands, 'start': rng.randint(1, 9), 'length': rng.randint(5, 8)}

    def solve(self, p):
        """Level by level: the set of numbers after 1, 2, … N commands."""
        level = {p['start']}
        for _ in range(p['length']):
            level = {apply(c, v) for v in level for c in p['commands']}
        if not 6 <= len(level) <= 10_000 or len(level) == len(p['commands']) ** p['length']:
            raise Rejected  # too few, or every program gives its own number (nothing to think about)
        return len(level)

    def check(self, p):
        """Run every program of N commands one by one."""
        results = set()
        for program in product(p['commands'], repeat=p['length']):
            v = p['start']
            for c in program:
                v = apply(c, v)
            results.add(v)
        return len(results)

    def word(self, p, answer):
        lines, words = describe(p['commands'])
        statement = (f'Исполнитель {p["name"]} преобразует целое число на экране. Его команды:\n{lines}\n'
                     f'Сколько различных чисел может оказаться на экране после выполнения программы, '
                     f'которая начинается с числа {p["start"]} и содержит ровно {p["length"]} команд?')
        ops = ', '.join(f'v {"+" if k == "add" else "*"} {n}' for k, n in p['commands'])
        solution = ('Разные программы могут давать одно и то же число, поэтому считаем не программы, а множество '
                    'результатов: после каждого шага заменяем множество чисел множеством всех их «потомков».\n\n'
                    f'level = {{{p["start"]}}}\nfor _ in range({p["length"]}):\n    level = {{w for v in level for w in ({ops})}}\n'
                    f'print(len(level))\n\nОтвет: {answer}')
        level = 'medium' if len(p['commands']) == 2 else 'hard'
        return statement, solution, level
