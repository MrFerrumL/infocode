"""Find and erase the author's task number ("270)") at the start of a condition snapshot.

K. Polyakov's documents number every task: "270) *(Д. Статный) Среди натуральных чисел…". The number hangs left of
the text (the following lines start further right). A snapshot sometimes also catches the end of the previous task
above it (cut off here), or the start of the next one below (kept: answer options «1) 2) 3)» look the same). The number itself stays in the database (Task.external_id), so the
catalogue can still sort in the author's order; pupils just do not see it in the picture.

Only Pillow is used: row and column ink profiles come from resizing a black-and-white copy to one pixel wide/high.
"""
from dataclasses import dataclass
from PIL import Image

INK = 150            # darker than this is text
LABEL_MIN_X, LABEL_MAX_X = 40, 105  # the number hangs near the left edge (snapshots are 1920 px wide);
                                   # shaded series intros start further left (≈25 px) and are not numbers
LABEL_MIN_W, LABEL_MAX_W = 14, 125
GAP = 8              # blank columns between the number and the text after it (justified lines leave ≈10)


@dataclass
class Line:
    top: int
    bottom: int
    left: int
    right: int


def _ink(image):
    return image.convert('L').point(lambda v: 255 if v < INK else 0)


def _runs(profile, min_gap=1):
    """[(start, end)] of non-zero stretches of a 1-D profile, joining gaps shorter than min_gap."""
    runs, start, blank = [], None, 0
    for i, v in enumerate(profile):
        if v:
            if start is None:
                start = i
            blank = 0
            end = i
        elif start is not None:
            blank += 1
            if blank >= min_gap:
                runs.append((start, end))
                start = None
    if start is not None:
        runs.append((start, end))
    return runs


def lines(ink):
    width, height = ink.size
    rows = list(ink.resize((1, height), Image.BOX).getdata())
    found = []
    for top, bottom in _runs(rows, min_gap=3):
        box = ink.crop((0, top, width, bottom + 1)).getbbox()
        if box and bottom - top >= 6:
            found.append(Line(top, bottom, box[0], box[2]))
    return found


def label_span(ink, line):
    """(left, right) of the number at the start of this line, or None if the line does not start with one."""
    if not LABEL_MIN_X <= line.left <= LABEL_MAX_X:
        return None
    strip = ink.crop((line.left, line.top, min(ink.size[0], line.left + LABEL_MAX_W + 60), line.bottom + 1))
    columns = list(strip.resize((strip.size[0], 1), Image.BOX).getdata())
    pieces = _runs(columns, min_gap=GAP)
    if len(pieces) < 2 and line.right - line.left > LABEL_MAX_W + 60:
        return None  # no clear gap after the first word
    first = pieces[0]
    width = first[1] - first[0] + 1
    if not LABEL_MIN_W <= width <= LABEL_MAX_W or line.right <= line.left + first[1] + GAP:
        return None  # the number is always followed by the condition on the same line
    return line.left + first[0], line.left + first[1]


@dataclass
class Plan:
    erase: tuple      # (left, top, right, bottom) of the number
    crop: tuple       # (top, bottom) of the task's own lines
    above: int        # lines of the previous task cut off above
    below: bool       # another number further down (answer options or the next task; kept as is)


def plan(image):
    """Where the number is and which rows belong to this task; None when no hanging number is found."""
    ink = _ink(image)
    found = lines(ink)
    labelled = [(i, span) for i, line in enumerate(found) if (span := label_span(ink, line))]
    if not labelled:
        return None
    # The task's own number is the first one; a second number below starts the next task.
    index, (left, right) = labelled[0]
    own = found[index]
    nxt = next((found[j] for j, _ in labelled if j > index), None)
    # A real number hangs left of the task's text: its next lines start further right.
    after = [l.left for l in found[index + 1:found.index(nxt) if nxt else None] if l.left > LABEL_MAX_X]
    indent = sorted(after)[len(after) // 2] if after else None
    if indent is not None and indent - left < 40:
        return None
    # Lines above are cut only when they are plain text at the same indent — the tail of the previous task.
    # Anything else above (a shaded series intro, a table, a picture) belongs to the condition and stays.
    above = found[:index]
    tail = bool(above) and indent is not None and all(abs(l.left - indent) <= 20 for l in above)
    if above and not tail:
        index_cut = 0
    else:
        index_cut = index
    top = 0 if index_cut == 0 else (found[index - 1].bottom + own.top) // 2
    # Nothing is cut below: numbered answer options («1)  2)  3)  4)») look just like the next task's number.
    bottom = image.size[1]
    return Plan((left - 3, own.top - 3, right + 4, own.bottom + 3), (top, bottom), index_cut, nxt is not None)


def clean(image):
    """A copy without the number (and without other tasks' lines); None when nothing was found."""
    found = plan(image)
    if not found:
        return None
    result = image.convert('RGB')
    background = result.getpixel((result.size[0] - 2, 2))
    result.paste(background, found.erase)
    top, bottom = found.crop
    return result.crop((0, top, result.size[0], bottom))
