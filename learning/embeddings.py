"""Sentence vectors made on the server by a small multilingual model, for «Похожие» and search by meaning.

The model (intfloat/multilingual-e5-small, exported to 8-bit ONNX by deploy/export_embedding_model.py) lives in
settings.EMBED_MODEL_DIR: model.onnx and tokenizer.json. Without it the site works as before: «Похожие» use the
precomputed links or tasks of the same number and difficulty.
"""
import hashlib
import html
import os
import re
import threading
from functools import lru_cache
from django.conf import settings

DIMENSIONS = 384
MAX_TOKENS = 256
BATCH = 32
_lock = threading.Lock()


def available():
    folder = settings.EMBED_MODEL_DIR
    if not ((folder / 'model.onnx').is_file() and (folder / 'tokenizer.json').is_file()):
        return False
    try:
        import numpy, onnxruntime, tokenizers  # noqa: F401,E401
    except ImportError:
        return False
    return True


@lru_cache(maxsize=1)
def _engine():
    import onnxruntime
    from tokenizers import Tokenizer
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = int(os.environ.get('EMBED_THREADS', '2'))  # the site keeps the other cores
    session = onnxruntime.InferenceSession(str(settings.EMBED_MODEL_DIR / 'model.onnx'), options, providers=['CPUExecutionProvider'])
    tokenizer = Tokenizer.from_file(str(settings.EMBED_MODEL_DIR / 'tokenizer.json'))
    tokenizer.enable_truncation(MAX_TOKENS)
    tokenizer.enable_padding(pad_id=tokenizer.token_to_id('<pad>') or 1, pad_token='<pad>')
    return session, tokenizer


def embed(texts):
    """Unit vectors (float32, n × 384) of the texts; e5 models expect the "query: " prefix."""
    import numpy
    session, tokenizer = _engine()
    out = []
    for start in range(0, len(texts), BATCH):
        encoded = tokenizer.encode_batch(['query: ' + t for t in texts[start:start + BATCH]])
        ids = numpy.array([e.ids for e in encoded], dtype=numpy.int64)
        mask = numpy.array([e.attention_mask for e in encoded], dtype=numpy.int64)
        with _lock:  # one model run at a time keeps memory and CPU use predictable
            hidden = session.run(None, {'input_ids': ids, 'attention_mask': mask})[0]
        weights = mask[..., None].astype(numpy.float32)
        vectors = (hidden * weights).sum(1) / numpy.maximum(weights.sum(1), 1e-9)
        out.append(vectors / numpy.linalg.norm(vectors, axis=1, keepdims=True))
    return numpy.vstack(out) if out else numpy.zeros((0, DIMENSIONS), dtype=numpy.float32)


def to_bytes(vector):
    import numpy
    return numpy.asarray(vector, dtype=numpy.float16).tobytes()


def matrix(blobs):
    import numpy
    return numpy.frombuffer(b''.join(blobs), dtype=numpy.float16).reshape(-1, DIMENSIONS).astype(numpy.float32)


def plain(value):
    value = html.unescape(re.sub(r'<[^>]+>', ' ', value or ''))
    value = re.sub(r'\\[()\[\]]', ' ', value)  # MathJax brackets
    return ' '.join(value.split())[:2000]


def task_text(task, source_text=''):
    """The words of a condition: its text and text blocks; pictures-only tasks use the text of their source."""
    parts = [task.statement] + [b.text for b in task.content_blocks.all() if b.purpose == 'statement' and b.kind in ('text', 'html', 'heading')]
    own = plain(' '.join(p for p in parts if p))
    return own if len(own) >= 20 else plain(source_text)


def digest(text):
    return hashlib.sha1(text.encode('utf-8')).hexdigest()
