"""Package complete selected categories with native captures and original attachments."""
import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import zipfile
from PIL import Image


FORMATS = ('infocode-kompege-numbers-v1', 'infocode-kompege-games-v1', 'infocode-kompege-variants-v1')


def package(folder, output):
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format') not in FORMATS:
        raise ValueError('Unknown manifest format')
    captures = json.loads((folder / 'capture-report.json').read_text(encoding='utf-8'))
    jobs = json.loads((folder / 'jobs.json').read_text(encoding='utf-8'))
    report = {(r['number'], r['id']): r for r in captures}
    # Game groups (19–21) reuse the condition capture, so look captures up by image name.
    states = {Path(job['image']).name: report.get((job['number'], job['id'])) for job in jobs}
    counts = Counter(r['number'] for r in manifest['tasks'])
    if counts != Counter({int(n): count for n, count in manifest['source_counts'].items()}) or set(report) != {(j['number'], j['id']) for j in jobs}:
        raise ValueError('Incomplete category captures')
    blobs = {}
    for row in manifest['tasks']:
        images = []
        for relative in row['images']:
            name = Path(relative).name
            data = (folder / 'captures' / name).read_bytes()
            state = states.get(name)
            if state is None:
                raise ValueError('Capture is not listed in jobs: ' + relative)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != 'JPEG' or image.width != 1920 or image.height != state['height']:
                    raise ValueError('Invalid native capture: ' + relative)
                image.verify()
            if state['broken'] or state['overflow'] or state['errors']:
                raise ValueError('Incomplete render: ' + relative)
            images.append({'path': relative, 'sha256': hashlib.sha256(data).hexdigest()})
            blobs[relative] = data
        row['images'] = images
        for item in row['files']:
            file = folder / item['path']
            if hashlib.sha256(file.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('Original attachment checksum mismatch')
            blobs[item['path']] = file  # written from disk; number 24 holds ~1.1 GB
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'))
        for name, data in blobs.items():
            if isinstance(data, Path):
                archive.write(data, name)
            else:
                archive.writestr(name, data)
    print(f'Packaged {dict(counts)}; {len(manifest["tasks"])} native screenshots ({sum(1 for k in blobs if k.startswith("screenshots/"))} unique), {sum(len(r["files"]) for r in manifest["tasks"])} files; {output.stat().st_size} bytes.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    package(args.folder, args.output)
