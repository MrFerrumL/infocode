"""Words of the pictures-only conditions (KOMPEGE, K. Polyakov) for the site's own sentence vectors.

    python deploy/export_task_texts.py WORK_DIR learning/data/task_texts.json.gz

Uses the same sources as deploy/build_similar_tasks.py; `manage.py embed_tasks --texts …` loads the file on the server.
Needs pymupdf (not used by the site itself).
"""
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from deploy.build_similar_tasks import kompege_texts, polyakov_texts  # noqa: E402


def main():
    work, out = Path(sys.argv[1]), Path(sys.argv[2])
    texts = {**kompege_texts(work), **polyakov_texts(work)}
    data = {key: text for key, (_, text) in sorted(texts.items()) if len(text) >= 20}
    out.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8'), 9))
    print(f'{out}: {len(data)} texts, {out.stat().st_size / 1e6:.1f} MB')


if __name__ == '__main__':
    main()
