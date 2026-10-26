import gzip
import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Prefetch, Q
from learning import embeddings, ocr
from learning.models import Task, TaskBlock, TaskVector


class Command(BaseCommand):
    help = ('Give every catalogue task and every task of a variant a sentence vector for «Похожие» (learning/embeddings.py). '
            'Only new or changed texts are embedded. --texts loads the words of pictures-only conditions '
            '(deploy/export_task_texts.py, kept out of git).')

    def add_arguments(self, parser):
        parser.add_argument('--texts', type=Path, help='task_texts.json.gz: {"source:number:external_id": text}')
        parser.add_argument('--limit', type=int, default=0)
        parser.add_argument('--ocr', action='store_true', help='Read the words of pictures-only conditions without a source text (Tesseract)')

    def handle(self, *args, **opts):
        if opts['texts']:
            self.load_texts(opts['texts'])
        if not embeddings.available():
            raise CommandError(f'No model in {embeddings.settings.EMBED_MODEL_DIR} (model.onnx, tokenizer.json) or onnxruntime is missing.')
        # Tasks of variants count too (KOMPEGE variants keep their own tasks outside the catalogue): «≈ Похожие варианты».
        in_variants = Task.all_objects.filter(variantitem__isnull=False).values('pk')
        tasks = (Task.all_objects.filter(Q(published=True, in_catalog=True) | Q(pk__in=in_variants), archived_at__isnull=True)
                 .select_related('topic', 'text_vector')
                 .prefetch_related(Prefetch('content_blocks', queryset=TaskBlock.all_objects.filter(kind__in=['text', 'html', 'heading'])))
                 .order_by('pk'))
        todo, skipped, read = [], 0, 0
        for task in tasks.iterator(chunk_size=500):
            row = getattr(task, 'text_vector', None)
            text = embeddings.task_text(task, row.source_text if row else '')
            if not text and opts['ocr'] and ocr.available():
                words = self.read_picture(task)
                if words:
                    row, _ = TaskVector.objects.update_or_create(task_id=task.pk, defaults={'source_text': words})
                    text, read = embeddings.task_text(task, words), read + 1
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
        self.stdout.write(f'\nNew or changed vectors: {len(todo)}; conditions read from pictures: {read}; '
                          f'tasks without words (pictures only, no source text): {skipped}.')

    def read_picture(self, task):
        """Words of the task's first condition picture ('' when it cannot be read)."""
        block = (TaskBlock.all_objects.filter(task=task, purpose='statement', kind='image').exclude(image='')
                 .order_by('position', 'id').first())
        if not block:
            return ''
        try:
            with block.image.open('rb') as picture:
                return embeddings.plain(ocr.read(picture.read()))
        except (OSError, ocr.Unreadable, ValueError):
            return ''

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
