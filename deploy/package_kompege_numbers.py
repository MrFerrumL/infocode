"""Package complete selected categories with native captures and original attachments."""
import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import zipfile
from PIL import Image


def package(folder, output):
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    captures = json.loads((folder / 'capture-report.json').read_text(encoding='utf-8'))
    report = {(r['number'], r['id']): r for r in captures}
    counts = Counter(r['number'] for r in manifest['tasks'])
    if counts != Counter({int(n): count for n, count in manifest['source_counts'].items()}) or len(report) != len(manifest['tasks']):
        raise ValueError('Incomplete category captures')
    blobs = {}
    for row in manifest['tasks']:
        relative = row['images'][0]
        data = (folder / 'captures' / Path(relative).name).read_bytes()
        state = report[(row['number'], row['source_id'])]
        with Image.open(io.BytesIO(data)) as image:
            if image.format != 'JPEG' or image.width != 1920 or image.height != state['height']:
                raise ValueError('Invalid native capture: ' + relative)
            image.verify()
        if state['broken'] or state['overflow'] or state['errors']:
            raise ValueError('Incomplete render: ' + relative)
        row['images'] = [{'path': relative, 'sha256': hashlib.sha256(data).hexdigest()}]
        blobs[relative] = data
        for item in row['files']:
            file = folder / item['path']
            data = file.read_bytes()
            if hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('Original attachment checksum mismatch')
            blobs[item['path']] = data
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'))
        for name, data in blobs.items():
            archive.writestr(name, data)
    print(f'Packaged {dict(counts)}; {len(manifest["tasks"])} native screenshots, {sum(len(r["files"]) for r in manifest["tasks"])} files; {output.stat().st_size} bytes.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    package(args.folder, args.output)
