"""Read-only checks of content and files, including issued and archived material."""
import shutil
from pathlib import Path
from django.apps import apps
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.db import connection, models
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Count, Q
from django.shortcuts import render
from django.views.decorators.http import require_GET
from .views import staff_required


def content_report():
    checks = []
    missing = []
    seen = set()

    def check(title, level, detail, link=''):
        checks.append({'title': title, 'level': level, 'detail': detail, 'link': link})

    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        executor = MigrationExecutor(connection)
        pending = len(executor.migration_plan(executor.loader.graph.leaf_nodes()))
    except Exception:
        check('База данных', 'error', 'Нет соединения. Проверьте настройки базы и запущен ли сервер базы данных.')
        return {'checks': checks, 'missing': [], 'missing_count': 0, 'checked_files': 0, 'has_errors': True}
    check('База данных', 'ok', 'Соединение работает.')
    check('Миграции', 'error' if pending else 'ok', f'Нужно применить миграций: {pending}.' if pending else 'Схема базы актуальна.')
    if pending:
        return {'checks': checks, 'missing': [], 'missing_count': 0, 'checked_files': 0, 'has_errors': True}

    def inspect_file(storage, name, source):
        if not name:
            return
        key = (id(storage), name)
        if key in seen:
            return
        seen.add(key)
        try:
            exists = storage.exists(name)
        except Exception:
            exists = False
        if not exists:
            missing.append({'name': name, 'source': source})

    # The base manager includes archived rows: issued variants may still use
    # their photos/files even after the original was removed from the catalogue.
    for model in apps.get_app_config('learning').get_models():
        fields = [field for field in model._meta.concrete_fields if isinstance(field, models.FileField)]
        if not fields:
            continue
        for row in model._base_manager.values_list('pk', *(field.name for field in fields)).iterator(chunk_size=200):
            for field, name in zip(fields, row[1:]):
                inspect_file(field.storage, name, f'{model._meta.verbose_name} · ID {row[0]}')
    from .models import VariantItem, Task, Lesson
    for pk, attachment, blocks in VariantItem.objects.values_list('pk', 'attachment', 'blocks').iterator(chunk_size=200):
        source = f'задание в выданном варианте · ID {pk}'
        inspect_file(default_storage, attachment, source)
        if isinstance(blocks, list):
            for block in blocks:
                if isinstance(block, dict):
                    for kind in ('image', 'file'):
                        value = block.get(kind)
                        if isinstance(value, str):
                            inspect_file(default_storage, value, source)
    check('Фото, видео и файлы', 'error' if missing else 'ok',
          f'Проверено файлов: {len(seen)}. Недоступно: {len(missing)}. Восстановите недостающие материалы из копии.' if missing else f'Проверено файлов: {len(seen)}. Все прикреплённые файлы найдены.')
    empty_tasks = Task.objects.filter(published=True, statement='', attachment='').annotate(
        blocks_count=Count('content_blocks', filter=Q(content_blocks__purpose='statement', content_blocks__archived_at=None))).filter(blocks_count=0).count()
    check('Опубликованные задания', 'warning' if empty_tasks else 'ok',
          f'Без условия: {empty_tasks}. Добавьте материал или переведите эти задания в черновики.' if empty_tasks else 'У всех опубликованных заданий есть условие.', '/teacher/tasks/?status=published')
    empty_lessons = Lesson.objects.filter(published=True, section__course__published=True).annotate(
        slides_count=Count('steps', filter=Q(steps__published=True, steps__archived_at=None))).filter(slides_count=0).count()
    check('Опубликованные уроки', 'warning' if empty_lessons else 'ok',
          f'Без опубликованных слайдов: {empty_lessons}. Добавьте слайды или снимите публикацию урока.' if empty_lessons else 'У всех опубликованных уроков есть слайды.', '/teacher/courses/')
    static_ready = all((Path(settings.STATIC_ROOT)/name).is_file() for name in ('app.css', 'workspace.css', 'task-bank.js'))
    check('Файлы оформления', 'ok' if static_ready else 'warning',
          'Собранные файлы оформления на месте.' if static_ready else 'После обновления выполните collectstatic или перезапустите start_local.py.')
    media = Path(settings.MEDIA_ROOT)
    existing = media
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    try:
        free = shutil.disk_usage(existing).free / 1024**3
        check('Место на диске', 'warning' if free < 2 else 'ok', f'Свободно: {free:.1f} ГБ. Предусмотрите место для материалов и очередной резервной копии.')
    except OSError:
        check('Место на диске', 'warning', 'Не удалось прочитать свободное место. Проверьте диск в панели сервера.')
    return {'checks': checks, 'missing': missing[:30], 'missing_count': len(missing), 'checked_files': len(seen),
            'has_errors': any(item['level'] == 'error' for item in checks)}


@staff_required
@require_GET
def platform_check(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    return render(request, 'platform/readiness.html', {'report': content_report(), 'nav': 'teacher'})
