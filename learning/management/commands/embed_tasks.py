import gzip
import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Prefetch
from learning import embeddings
from learning.models import Task, TaskBlock, TaskVector


class Command(BaseCommand):
    help = ('Give every catalogue task a sentence vector for «Похожие» (learning/embeddings.py). Only new or changed texts '
            'are embedded. --texts loads the words of pictures-only conditions (deploy/export_task_texts.py).')

    def add_arguments(self, parser):
        parser.add_argument('--texts', type=Path, help='task_texts.json.gz: {"source:number:external_id": text}')
        parser.add_argument('--limit', type=int, default=0)

    def handle(self, *args, **opts):
        if opts['texts']:
            self.load_texts(opts['texts'])
        if not embeddings.available():
            raise CommandError(f'No model in {embeddings.settings.EMBED_MODEL_DIR} (model.onnx, tokenizer.json) or onnxruntime is missing.')
        tasks = (Task.all_objects.filter(published=True, in_catalog=True, archived_at__isnull=True).select_related('topic', 'text_vector')
                 .prefetch_related(Prefetch('content_blocks', queryset=TaskBlock.all_objects.filter(kind__in=['text', 'html', 'heading'])))
                 .order_by('pk'))
        todo, skipped = [], 0
        for task in tasks.iterator(chunk_size=500):
            row = getattr(task, 'text_vector', None)
            text = embeddings.task_text(task, row.source_text if row else '')
            if not text:
                skipped += 1
                continue
            digest = embeddings.digest(text)
            if not row or row.digest != digest or row.vector is None:
                todo.append((task.pk, text, digest))
            if opts['limit'] and len(todo) >= opts['limit']:
                break
        for start in range(0, len(todo), 256):
            part = todo[start:start + 256]
            vectors = embeddings.embed([text for _, text, _ in part])
            for (pk, _, digest), vector in zip(part, vectors):
                TaskVector.objects.update_or_create(task_id=pk, defaults={'digest': digest, 'vector': embeddings.to_bytes(vector)})
            self.stdout.write(f'  embedded {min(start + 256, len(todo))} of {len(todo)}', ending='\r')
        self.stdout.write(f'\nNew or changed vectors: {len(todo)}; tasks without words (pictures only, no source text): {skipped}.')

    def load_texts(self, path):
        data = json.loads(gzip.decompress(path.read_bytes()))
        keys = {f'{s}:{n}:{e}': pk for pk, s, n, e in Task.all_objects.filter(external_id__isnull=False)
                .values_list('pk', 'source_system', 'topic__number', 'external_id')}
        found = 0
        for key, text in data.items():
            pk = keys.get(key)
            if pk:
                TaskVector.objects.update_or_create(task_id=pk, defaults={'source_text': text})
                found += 1
        self.stdout.write(f'Source texts: {found} of {len(data)} matched tasks of this bank.')
