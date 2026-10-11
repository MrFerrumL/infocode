"""Bounded, cached previews. Called only after the original file's access check."""
import hashlib
import os
import tempfile
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError


def preview_path(path, root):
    stat = path.stat()
    digest = hashlib.sha256(f'{path}:{stat.st_mtime_ns}:{stat.st_size}:1600'.encode()).hexdigest()
    folder = root / '.previews'
    folder.mkdir(exist_ok=True)
    target = folder / (digest + '.webp')
    if not target.exists():
        try:
            with Image.open(path) as source:
                if source.width * source.height > 40_000_000:
                    return path
                picture = ImageOps.exif_transpose(source)
                picture.thumbnail((1600, 1600))
                picture = picture.convert('RGBA' if 'A' in picture.getbands() else 'RGB')
                with tempfile.NamedTemporaryFile(dir=folder, suffix='.webp', delete=False) as temp:
                    temp_path = Path(temp.name)
                try:
                    picture.save(temp_path, format='WEBP', quality=84, method=4)
                    os.replace(temp_path, target)
                finally:
                    temp_path.unlink(missing_ok=True)
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
            return path
    return target
