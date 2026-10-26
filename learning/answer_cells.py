"""Answers of several numbers get separate cells, as on the exam: № 17 and № 26 — two numbers, № 27 — two numbers
for file A and two for file B. The cells are joined into one answer ("A1 A2 B1 B2") before the usual check."""

ROW_LABELS = {27: ['Файл A', 'Файл B']}


def shape(task):
    """(rows, columns, row labels) for a task whose answer needs cells, otherwise None."""
    topic = getattr(task, 'topic', None)
    if not topic or getattr(topic, 'subject', 'inf') != 'inf':
        return None
    parts = len(task.answer.split())
    number = topic.number
    if number in (17, 26) and parts == 2:
        return 1, 2, None
    if number == 27 and parts == 4:
        return 2, 2, ROW_LABELS[27]
    if number == 27 and parts == 2:
        return 1, 2, None
    return None


def grid(task, prefix):
    """Rows of (input name, label) for the template; None when the task takes one plain answer."""
    found = shape(task)
    if not found:
        return None
    rows, cols, labels = found
    return [{'label': labels[r] if labels else '', 'cells': [f'{prefix}-cell-{r}-{c}' for c in range(cols)]} for r in range(rows)]


def join(data, task, prefix):
    """Put the cells' values into the answer field of a copy of the POST data (row by row, left to right)."""
    found = shape(task)
    names = [f'{prefix}-cell-{r}-{c}' for r in range(found[0]) for c in range(found[1])] if found else []
    if not names or not any(name in data for name in names):
        return data
    data = data.copy()
    data[f'{prefix}-answer'] = ' '.join(data.get(name, '').strip() for name in names).strip()
    return data
