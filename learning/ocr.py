"""Text of a task photo or condition picture, read by Tesseract (installed in the Docker image, Russian + English).

Used by search by photo (learning/semantic_search.py) and to give words to pictures-only conditions
(`manage.py embed_tasks --ocr`). Without Tesseract the site simply offers no photo search.
"""
import shutil
import subprocess
from io import BytesIO
from PIL import Image, ImageOps

MAX_BYTES = 10 * 1024 * 1024
MAX_SIDE = 4000


class Unreadable(Exception):
    pass


def available():
    return shutil.which('tesseract') is not None


def prepare(data):
    """A clean grey picture Tesseract reads well: small photos are enlarged, big ones reduced."""
    if len(data) > MAX_BYTES:
        raise Unreadable('Фото больше 10 МБ — уменьшите его.')
    try:
        image = Image.open(BytesIO(data))
        image.load()
    except Exception as exc:
        raise Unreadable('Это не похоже на картинку (подходят JPG, PNG, WEBP).') from exc
    image = ImageOps.exif_transpose(image).convert('L')
    width, height = image.size
    if max(width, height) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE))
    elif width < 1200:
        factor = 1200 / width
        image = image.resize((1200, max(1, round(height * factor))), Image.LANCZOS)
    image = ImageOps.autocontrast(image)
    out = BytesIO()
    image.save(out, 'PNG')
    return out.getvalue()


def read(data, timeout=40):
    """Recognised text of the picture ('' when there is none)."""
    if not available():
        raise Unreadable('Распознавание текста не установлено на сервере.')
    result = subprocess.run(['tesseract', 'stdin', 'stdout', '-l', 'rus+eng', '--psm', '6'], input=prepare(data),
                            capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise Unreadable('Не удалось распознать текст на фото.')
    return ' '.join(result.stdout.decode('utf-8', 'replace').split())
