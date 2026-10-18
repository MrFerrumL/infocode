"""Bring a Colab / Jupyter notebook (.ipynb) into «Конспекты», cell by cell, so it reads like in Colab.

- Markdown cells become text cells (Markdown → HTML → the platform's safe HTML vocabulary);
  pictures inside them (`![…](data:image/png;base64,…)` or Colab attachments) become picture cells.
- Code cells keep their code and what it printed in Colab (shown under the code); pictures they drew
  become picture cells. Code is never executed on the site.

Importing again into the same notebook replaces its cells; course slides that use it follow the new version.
"""
import base64
import binascii
import io
import json
import re
from pathlib import PurePosixPath
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.html import strip_tags
import markdown
from PIL import Image
from .notebook_models import Notebook, NotebookCell
from .rich_text import sanitize_html

MAX_BYTES = 60 * 1024 * 1024
MAX_CELLS = 5000
MAX_OUTPUT = 20000
IMAGE = re.compile(r'!\[([^\]]*)\]\((data:image/(png|jpe?g|gif|webp);base64,([A-Za-z0-9+/=\s]+)|attachment:([^)\s]+))\)')
HTML_IMAGE = re.compile(r'<img\b[^>]*\bsrc="(data:image/(png|jpe?g|gif|webp);base64,([A-Za-z0-9+/=\s]+))"[^>]*>', re.I)


def source(cell):
    value = cell.get('source', '')
    return ''.join(value) if isinstance(value, list) else str(value or '')


BLOCK_START = re.compile(r'\s*(?:\d+[.)]|[-*+])\s|\s*>|\s*#{1,6}\s|\s*\|')


def colab_blocks(text):
    """Colab starts a list, quote, heading or table right after a line of text; Python-Markdown needs a blank line."""
    lines, fenced, previous = [], False, ''
    for line in text.split('\n'):
        if line.lstrip().startswith('```'):
            fenced = not fenced
        elif not fenced and BLOCK_START.match(line) and previous.strip() and not BLOCK_START.match(previous):
            lines.append('')
        lines.append(line)
        previous = line
    return '\n'.join(lines)


def to_html(text):
    html = markdown.markdown(colab_blocks(text), extensions=['fenced_code', 'tables', 'sane_lists'], output_format='html')
    return sanitize_html(html)


def picture(data, alt):
    """Validated image bytes → (ContentFile, caption); pictures are stored as PNG or JPEG."""
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        image = Image.open(io.BytesIO(data))
        if image.width * image.height > 40_000_000:
            raise ValueError('слишком большая картинка')
        fmt = 'JPEG' if image.format == 'JPEG' else 'PNG'
        out = io.BytesIO()
        (image.convert('RGB') if fmt == 'JPEG' else image).save(out, fmt, optimize=True)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError(f'Картинка «{alt or "без подписи"}» не открывается: {exc}') from exc
    return out.getvalue(), 'jpg' if fmt == 'JPEG' else 'png'


def decode(payload):
    try:
        return base64.b64decode(re.sub(r'\s+', '', payload), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError('Картинка в ячейке повреждена.') from exc


def markdown_parts(cell):
    """[('text', html) | ('image', bytes, alt)] in the order they appear in a Markdown cell."""
    text, attachments, parts, at = source(cell), cell.get('attachments') or {}, [], 0
    matches = sorted(list(IMAGE.finditer(text)) + list(HTML_IMAGE.finditer(text)), key=lambda m: m.start())
    for match in matches:
        if match.start() < at:
            continue
        parts.append(('text', text[at:match.start()]))
        if match.re is HTML_IMAGE:
            parts.append(('image', decode(match.group(3)), ''))
        elif match.group(5):  # Colab attachment: ![name](attachment:image.png)
            files = attachments.get(match.group(5)) or {}
            payload = next((v for k, v in files.items() if k.startswith('image/')), None)
            if payload is None:
                raise ValueError(f'Нет вложения {match.group(5)}.')
            parts.append(('image', decode(payload if isinstance(payload, str) else ''.join(payload)), match.group(1)))
        else:
            parts.append(('image', decode(match.group(4)), match.group(1)))
        at = match.end()
    parts.append(('text', text[at:]))
    result = []
    for part in parts:
        if part[0] == 'text':
            html = to_html(part[1]) if part[1].strip() else ''
            if strip_tags(html).strip():
                result.append(('text', html))
        else:
            result.append(part)
    return result


def code_parts(cell):
    """[('code', code, output)] plus pictures the code drew."""
    printed, images = [], []
    for out in cell.get('outputs') or []:
        kind = out.get('output_type')
        if kind == 'stream':
            printed.append(''.join(out.get('text', '')) if isinstance(out.get('text'), list) else str(out.get('text', '')))
        elif kind in ('execute_result', 'display_data'):
            data = out.get('data') or {}
            png = next((data[k] for k in ('image/png', 'image/jpeg') if k in data), None)
            if png is not None:
                images.append(('image', decode(png if isinstance(png, str) else ''.join(png)), 'Результат выполнения'))
            elif 'text/plain' in data:
                value = data['text/plain']
                printed.append((''.join(value) if isinstance(value, list) else str(value)) + '\n')
        elif kind == 'error':
            printed.append(f"{out.get('ename', 'Error')}: {out.get('evalue', '')}\n")
    output = ''.join(printed)
    if len(output) > MAX_OUTPUT:
        output = output[:MAX_OUTPUT] + '\n… (вывод сокращён)'
    code = source(cell).rstrip()
    return ([('code', code, output.rstrip('\n'))] if code.strip() else []) + images


def read(data):
    if len(data) > MAX_BYTES:
        raise ValueError('Файл больше 60 МБ.')
    try:
        nb = json.loads(data.decode('utf-8-sig'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Это не файл .ipynb (Colab / Jupyter).') from exc
    if not isinstance(nb, dict) or not isinstance(nb.get('cells'), list) or nb.get('nbformat', 4) < 4:
        raise ValueError('Нужен файл .ipynb формата Jupyter 4 (так сохраняет Colab).')
    parts = []
    for cell in nb['cells']:
        if not isinstance(cell, dict):
            continue
        if cell.get('cell_type') == 'markdown':
            parts += markdown_parts(cell)
        elif cell.get('cell_type') == 'code':
            parts += code_parts(cell)
    if not parts:
        raise ValueError('В файле нет ячеек.')
    if len(parts) > MAX_CELLS:
        raise ValueError(f'Слишком много ячеек: {len(parts)} (можно до {MAX_CELLS}).')
    return parts


def title_of(parts):
    for part in parts:
        if part[0] == 'text':
            match = re.search(r'<h2>(.*?)</h2>', part[1])
            if match:
                return strip_tags(match.group(1)).strip()[:180]
            break
    return ''


@transaction.atomic
def import_notebook(data, owner, title='', notebook=None, filename='notebook.ipynb', publish=None, group=None):
    """Create a notebook from .ipynb bytes, or replace the cells of `notebook`. Returns (notebook, cells)."""
    parts = read(data)
    created = notebook is None
    if created:
        notebook = Notebook(owner=owner, title=(title or title_of(parts) or PurePosixPath(filename).stem)[:180], group=group)
    elif title:
        notebook.title = title[:180]
    if publish is not None:
        notebook.published = publish
    if not created:
        notebook.revision += 1
    notebook.edit_version += 1
    notebook._cell_count = len(parts)
    notebook.full_clean()
    notebook.save()
    if not created:
        notebook.cells.all().delete()
        from .notebooks import invalidate_notebook_steps
        invalidate_notebook_steps(notebook, unpublish=not notebook.published)
    cells = []
    for position, part in enumerate(parts, 1):
        cell = NotebookCell(notebook=notebook, position=position, kind=part[0])
        if part[0] == 'text':
            cell.text = part[1]
        elif part[0] == 'code':
            cell.text, cell.output = part[1], part[2]
        else:
            data_bytes, ext = picture(part[1], part[2])
            cell.caption = part[2][:300]
            cell.image.save(f'colab-{notebook.pk}-{position}.{ext}', ContentFile(data_bytes), save=False)
        cells.append(cell)
    NotebookCell.objects.bulk_create(cells, batch_size=500)
    return notebook, len(cells)
