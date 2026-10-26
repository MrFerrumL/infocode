"""Turn the multilingual sentence-embedding model into a small ONNX file the site runs itself (learning/embeddings.py).

    python deploy/export_embedding_model.py OUT_DIR

Reads intfloat/multilingual-e5-small from the local Hugging Face cache (no network), exports it to ONNX, quantises the
weights to 8 bits and checks that the small model gives the same vectors as the original (cosine ≥ 0.98).
OUT_DIR gets model.onnx and tokenizer.json; deploy/install_kompege_pilot.py --model OUT_DIR puts them on the server.
Needs torch, transformers, onnx and onnxruntime (not used by the site itself, which only needs onnxruntime).
"""
import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault('HF_HUB_OFFLINE', '1')
import numpy
import torch
from huggingface_hub import snapshot_download
from onnxruntime import InferenceSession
from onnxruntime.quantization import QuantType, quantize_dynamic
from transformers import AutoModel, AutoTokenizer

MODEL = 'intfloat/multilingual-e5-small'
SAMPLES = ['query: Сколько существует программ, которые число 3 преобразуют в число 20?',
           'query: Определите количество простых чисел в диапазоне [2; 20000].',
           'query: Тело массой 2 кг движется вдоль оси Ox. Выберите все верные утверждения.']


class Encoder(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, input_ids, attention_mask):
        return self.model(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state


def pooled(hidden, mask):
    mask = mask[..., None].astype(numpy.float32)
    vectors = (hidden * mask).sum(1) / numpy.maximum(mask.sum(1), 1e-9)
    return vectors / numpy.linalg.norm(vectors, axis=1, keepdims=True)


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    source = Path(snapshot_download(MODEL, local_files_only=True))
    tokenizer = AutoTokenizer.from_pretrained(source)
    model = AutoModel.from_pretrained(source).eval()
    batch = tokenizer(SAMPLES, padding=True, truncation=True, max_length=256, return_tensors='pt')
    full = out / 'model-fp32.onnx'
    torch.onnx.export(Encoder(model), (batch['input_ids'], batch['attention_mask']), str(full), opset_version=17,
                      input_names=['input_ids', 'attention_mask'], output_names=['last_hidden_state'],
                      dynamic_axes={'input_ids': {0: 'batch', 1: 'tokens'}, 'attention_mask': {0: 'batch', 1: 'tokens'},
                                    'last_hidden_state': {0: 'batch', 1: 'tokens'}}, dynamo=False)
    small = out / 'model.onnx'
    quantize_dynamic(str(full), str(small), weight_type=QuantType.QInt8, per_channel=True)
    full.unlink()
    shutil.copy(source / 'tokenizer.json', out / 'tokenizer.json')
    with torch.no_grad():
        reference = pooled(model(**batch).last_hidden_state.numpy(), batch['attention_mask'].numpy())
    session = InferenceSession(str(small), providers=['CPUExecutionProvider'])
    hidden = session.run(None, {'input_ids': batch['input_ids'].numpy(), 'attention_mask': batch['attention_mask'].numpy()})[0]
    agreement = (pooled(hidden, batch['attention_mask'].numpy()) * reference).sum(1)
    print(f'{small}: {small.stat().st_size / 1e6:.0f} MB; cosine with the original: {", ".join(f"{a:.4f}" for a in agreement)}')
    if agreement.min() < 0.98:
        raise SystemExit('The quantised model drifts too far from the original.')


if __name__ == '__main__':
    main()
