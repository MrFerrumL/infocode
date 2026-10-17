"""Package verified native screenshots, exact answers and original source metadata."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile
from PIL import Image


def package(folder, output):
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    report = {r['id']: r for r in json.loads((folder / 'capture-report.json').read_text(encoding='utf-8'))}
    blobs = {}
    for row in manifest['tasks']:
        relative = row['images'][0]
        data = (folder / 'captures' / Path(relative).name).read_bytes()
        state = report[row['source_id']]
        with Image.open(io.BytesIO(data)) as image:
            if image.format != 'JPEG' or image.width != 1920 or image.height != state['height']:
                raise ValueError('Invalid capture: ' + relative)
            image.verify()
        if state['broken'] or state['overflow'] or state['errors']:
            raise ValueError('Incomplete render: ' + relative)
        row['images'] = [{'path': relative, 'sha256': hashlib.sha256(data).hexdigest()}]
        blobs[relative] = data
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'))
        for name, data in blobs.items():
            archive.writestr(name, data)
    print(f'Packaged {len(manifest["tasks"])} complete number-1 tasks, {len(blobs)} native screenshots; {output.stat().st_size} bytes.')


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('folder', type=Path)
    cli.add_argument('output', type=Path)
    args = cli.parse_args()
    package(args.folder, args.output)
