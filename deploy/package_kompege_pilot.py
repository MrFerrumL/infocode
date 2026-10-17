"""Package captured pilot images and files; generate an answer-free review page."""
import argparse
from collections import Counter
import hashlib
from html import escape
import json
from pathlib import Path
import zipfile

from PIL import Image


def package(folder, output):
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    tasks = manifest['tasks']
    if len(tasks) != 135 or Counter(row['number'] for row in tasks) != Counter({n: 5 for n in range(1, 28)}):
        raise ValueError('The pilot must contain exactly five tasks for every number.')
    assets = {}
    for row in tasks:
        images = []
        for item in row['images']:
            name = item if isinstance(item, str) else item['path']
            path = folder / name
            with Image.open(path) as image:
                if image.format not in ('PNG', 'JPEG') or image.width < 20 or image.height < 15:
                    raise ValueError('Invalid screenshot: ' + name)
                image.verify()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            images.append({'path': name, 'sha256': digest})
            assets[name] = path
        row['images'] = images
        for item in row['files']:
            path = folder / item['path']
            if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('Attachment checksum mismatch')
            assets[item['path']] = path
    # Only selected screenshots, attachments and necessary metadata enter the bundle.
    data = {key: manifest[key] for key in ('format', 'created_at', 'source', 'rights', 'topic_titles', 'tasks')}
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_suffix('.preparing.zip')
    with zipfile.ZipFile(staging, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr('manifest.json', json.dumps(data, ensure_ascii=False, indent=2))
        for name, path in sorted(assets.items()):
            archive.write(path, name)
    with zipfile.ZipFile(staging) as archive:
        if archive.testzip() is not None:
            raise ValueError('Archive integrity verification failed')
    staging.replace(output)
    parts = ['<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>КОМПЕГЭ · пробная подборка</title>',
             '<style>body{margin:0;background:#f5f6fa;color:#222738;font:16px/1.5 Arial,sans-serif}header,main{max-width:1160px;margin:auto;padding:24px}header p{color:#626b80}nav{display:flex;gap:8px;flex-wrap:wrap}nav a{background:#fff;border:1px solid #d8dceb;border-radius:8px;padding:7px 12px;text-decoration:none;color:#5a42cd}section{scroll-margin-top:16px}article{background:white;border:1px solid #dde1eb;border-radius:16px;margin:18px 0;padding:22px}h1{font-size:32px}h2{margin-top:36px}h3{margin-top:0}.meta{color:#697286}.condition{display:block;width:100%;height:auto;margin:16px 0;border:1px solid #eee}.files a{display:inline-block;margin:5px 12px 5px 0}a{color:#5a42cd}.badge{background:#eee9ff;padding:4px 9px;border-radius:8px}.note{background:#eee9ff;border-radius:12px;padding:16px}footer{padding:24px;text-align:center;color:#697286}</style>',
             '<header><span class="badge">ПРОБНАЯ ПОДБОРКА</span><h1>135 заданий · 27 номеров</h1><p>По пять заданий каждого типа. Условия — снимки исходных страниц, без переписывания текста. Ответы в этом предпросмотре скрыты.</p><nav>']
    parts.extend(f'<a href="#number-{n}">{n:02}</a>' for n in range(1, 28))
    parts.append('</nav></header><main>')
    for number in range(1, 28):
        title = data['topic_titles'].get(str(number), '')
        parts.append(f'<section id="number-{number}"><h2>№ {number} · {escape(title)}</h2>')
        for row in tasks:
            if row['number'] != number:
                continue
            parts.append(f'<article><h3>КОМПЕГЭ №{row["source_id"]}</h3><p class="meta">Номер ЕГЭ: {number} · {escape(row["author"])}</p>')
            if number in (20, 21):
                parts.append(f'<p class="note">Для этой карточки ответьте на вопрос задания {number}, приведённый после общего условия.</p>')
            for item in row['images']:
                parts.append(f'<img class="condition" loading="lazy" src="{escape(item["path"], quote=True)}" alt="Условие задания {number}, КОМПЕГЭ {row["source_id"]}">')
            parts.append('<div class="files">')
            for item in row['files']:
                parts.append(f'<a download="{escape(item["name"], quote=True)}" href="{escape(item["path"], quote=True)}">↓ {escape(item["name"])}</a>')
            parts.append(f'</div><a href="{escape(row["source_url"], quote=True)}" target="_blank" rel="noopener">Открыть оригинал ↗</a></article>')
        parts.append('</section>')
    parts.append('</main><footer>КОМПЕГЭ · источник и авторы сохранены. Пробный пакет ограничен 135 карточками.</footer></html>')
    (folder / 'preview.html').write_text(''.join(parts), encoding='utf-8')
    print(f'Pilot package verified: {len(tasks)} cards, {len(assets)} media files, {output.stat().st_size} bytes. Preview: {folder / "preview.html"}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    package(args.folder.resolve(), args.output.resolve())
