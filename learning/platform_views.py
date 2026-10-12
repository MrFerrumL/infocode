import csv
import hashlib
import io
import json
from datetime import timedelta
from pathlib import Path
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.core.validators import FileExtensionValidator
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse, resolve, Resolver404
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods
from .views import staff_required
from .models import (Task, TaskBlock, Course, Section, Lesson, Step, Topic, StudyGroup, StudentAssignment,
    Notification, MediaAsset, GroupInvite, InviteUse, TrashEntry, AuditEvent, ImportBatch, EditorDraft, VideoPosition, file_size, video_size)
from .assignments import own_groups, recipients_for, notify
from .course_services import can_edit, editable_courses
from .forms import UploadModelForm


class MediaForm(UploadModelForm):
    class Meta:
        model = MediaAsset
        fields = ['title', 'kind', 'file']
    def clean(self):
        d = super().clean()
        f, kind = d.get('file'), d.get('kind')
        if f and kind:
            extensions = {'image': ['png','jpg','jpeg','webp'], 'video': ['mp4','webm'], 'file': ['txt','csv','xlsx','xls','pdf','zip','docx','ods','odt','py']}
            FileExtensionValidator(extensions[kind])(f)
            (video_size if kind == 'video' else file_size)(f)
            if kind == 'image':
                from PIL import Image
                try:
                    Image.open(f).verify()
                except Exception:
                    self.add_error('file', 'Файл не является корректным изображением.')
                finally:
                    f.seek(0)
        return d


@staff_required
def library(request):
    form = MediaForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        asset = form.save(commit=False)
        asset.owner = request.user
        asset.save()
        messages.success(request, 'Материал добавлен. Его можно выбрать в редакторе блока или шага.')
        return redirect('media_library')
    assets = MediaAsset.objects.all() if request.user.is_superuser else MediaAsset.objects.filter(owner=request.user)
    q = request.GET.get('q', '').strip()
    if q:
        assets = assets.filter(title__icontains=q)
    return render(request, 'platform/library.html', {'form': form, 'page': Paginator(assets.order_by('-id'), 24).get_page(request.GET.get('page')), 'query': q, 'nav': 'teacher'})


@login_required
def notifications(request):
    qs = Notification.objects.filter(user=request.user)
    if request.method == 'POST':
        if request.POST.get('pk', '').isdigit():
            qs.filter(pk=request.POST['pk']).update(read_at=timezone.now())
        else:
            qs.filter(read_at=None).update(read_at=timezone.now())
        return redirect('notifications')
    return render(request, 'platform/notifications.html', {'page': Paginator(qs, 30).get_page(request.GET.get('page')), 'nav': 'notifications'})


class GroupForm(forms.ModelForm):
    class Meta:
        model = StudyGroup
        fields = ['title', 'students']
        widgets = {'students': forms.CheckboxSelectMultiple}
    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['students'].queryset = recipients_for(user)


@staff_required
@transaction.atomic
def groups(request):
    selected = get_object_or_404(own_groups(request.user), pk=request.GET['edit']) if request.GET.get('edit','').isdigit() else None
    form = GroupForm(request.POST or None, user=request.user, instance=selected)
    if request.method == 'POST':
        action = request.POST.get('action', 'save')
        if action == 'invite':
            group = get_object_or_404(own_groups(request.user), pk=request.POST.get('group'))
            GroupInvite.objects.create(group=group, teacher=request.user, expires_at=timezone.now()+timedelta(days=7))
            return redirect('teacher_groups')
        if action == 'revoke':
            invite = get_object_or_404(GroupInvite, pk=request.POST.get('invite'), teacher=request.user)
            invite.active = False
            invite.save(update_fields=['active'])
            return redirect('teacher_groups')
        if form.is_valid():
            group = form.save(commit=False)
            group.teacher = group.teacher or request.user
            group.save()
            form.save_m2m()
            return redirect('teacher_groups')
    invites = GroupInvite.objects.filter(teacher=request.user).select_related('group').order_by('-id')[:30]
    for i in invites:
        i.link = request.build_absolute_uri(reverse('join_group', args=[i.token]))
    return render(request, 'platform/groups.html', {'form': form, 'selected': selected, 'groups': own_groups(request.user).prefetch_related('students'), 'invites': invites, 'nav': 'teacher'})


@transaction.atomic
def join(request, token):
    invite = get_object_or_404(GroupInvite.objects.select_for_update().select_related('group','teacher'), token=token)
    already = request.user.is_authenticated and InviteUse.objects.filter(invite=invite, student=request.user).exists()
    valid = invite.active and invite.expires_at > timezone.now() and invite.uses < invite.max_uses
    if request.method == 'POST' and not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.path)
    if request.method == 'POST':
        if request.user.is_staff:
            messages.error(request, 'Приглашение предназначено для ученика.')
        elif already:
            messages.info(request, 'Вы уже приняли это приглашение.')
        elif not valid:
            messages.error(request, 'Приглашение закрыто, просрочено или исчерпано.')
        else:
            InviteUse.objects.create(invite=invite, student=request.user)
            invite.group.students.add(request.user)
            StudentAssignment.objects.get_or_create(teacher=invite.teacher, student=request.user)
            invite.uses += 1
            invite.save(update_fields=['uses'])
            notify(invite.teacher, 'Новый ученик в группе', f'{request.user.username} · {invite.group.title}', reverse('teacher_student', args=[request.user.pk]))
            messages.success(request, 'Вы добавлены в группу и закреплены за преподавателем.')
            return redirect('my_assignments')
    return render(request, 'platform/join.html', {'invite': invite, 'valid': valid, 'already': already})


ARCHIVABLE = {m._meta.label_lower: m for m in (Task, TaskBlock, Course, Section, Lesson, Step)}


@transaction.atomic
def archive(user, obj):
    current = type(obj).all_objects.select_for_update().get(pk=obj.pk)
    if current.archived_at:
        return False
    current.archived_at = timezone.now()
    fields = ['archived_at']
    if hasattr(current, 'published'):
        current.published = False
        fields.append('published')
    current.save(update_fields=fields)
    obj.archived_at = current.archived_at
    if hasattr(current, 'published'):
        obj.published = current.published
    TrashEntry.objects.create(user=user, model=current._meta.label_lower, object_id=current.pk, title=str(current)[:240])
    return True


@staff_required
@transaction.atomic
def trash(request):
    from urllib.parse import urlencode
    labels = {'learning.task': 'Задания', 'learning.taskblock': 'Блоки заданий',
              'learning.course': 'Курсы', 'learning.section': 'Разделы',
              'learning.lesson': 'Уроки', 'learning.step': 'Слайды'}
    rows = TrashEntry.objects.filter(restored_at=None)
    if not request.user.is_superuser:
        rows = rows.filter(user=request.user)
    if request.method == 'POST':
        entry = get_object_or_404(rows.select_for_update(), pk=request.POST.get('pk'))
        model = ARCHIVABLE.get(entry.model)
        obj = get_object_or_404(model.all_objects.select_for_update(), pk=entry.object_id) if model else None
        if not obj:
            raise Http404
        # Ownership is rechecked because the object may have changed hands.
        course = obj if isinstance(obj, Course) else obj.course if isinstance(obj, Section) else obj.section.course if isinstance(obj, Lesson) else obj.lesson.section.course if isinstance(obj, Step) else None
        if course and not can_edit(request.user, course):
            raise PermissionDenied
        if obj.archived_at:
            obj.archived_at = None
            fields = ['archived_at']
            if hasattr(obj, 'published'):
                obj.published = False
                fields.append('published')
            obj.save(update_fields=fields)
        entry.restored_at = timezone.now()
        entry.save(update_fields=['restored_at'])
        messages.success(request, 'Восстановлено. Проверьте содержимое и опубликуйте его заново. Если родитель в корзине, сначала восстановите его.')
        params = {key: request.GET[key][:200] for key in ('q', 'kind') if request.GET.get(key)}
        return redirect(reverse('trash') + ('?' + urlencode(params) if params else ''))
    query = request.GET.get('q', '').strip()[:200]
    kind = request.GET.get('kind', '')
    if query:
        rows = rows.filter(title__icontains=query)
    if kind in labels:
        rows = rows.filter(model=kind)
    else:
        kind = ''
    page = Paginator(rows.order_by('-deleted_at'), 30).get_page(request.GET.get('page'))
    for entry in page:
        entry.type_label = labels.get(entry.model, 'Материал')
    return render(request, 'platform/trash.html', {
        'page': page, 'query': query, 'kind': kind, 'types': labels.items(),
        'pagination_query': urlencode({key: value for key, value in {'q': query, 'kind': kind}.items() if value}), 'nav': 'teacher',
    })


@staff_required
def audit(request):
    qs = AuditEvent.objects.select_related('user')
    if not request.user.is_superuser:
        qs = qs.filter(user=request.user)
    return render(request, 'platform/audit.html', {'page': Paginator(qs, 30).get_page(request.GET.get('page')), 'nav': 'teacher'})


@staff_required
@require_POST
@transaction.atomic
def bulk_tasks(request):
    from .task_management import task_return_url
    return_to = task_return_url(request)
    ids = request.POST.getlist('selected')
    if len(ids)>100 or any(not pk.isdigit() for pk in ids):
        raise PermissionDenied
    tasks = list(Task.objects.select_for_update().filter(pk__in=ids).prefetch_related('content_blocks'))
    action = request.POST.get('action')
    level = request.POST.get('difficulty')
    if action not in ('archive', 'publish', 'draft', 'difficulty') or (action == 'difficulty' and level not in dict(Task.LEVELS)):
        messages.error(request, 'Выберите допустимое действие и сложность.')
        return redirect(return_to)
    if not tasks:
        messages.error(request, 'Выберите хотя бы одно доступное задание.')
        return redirect(return_to)
    if action == 'archive' and request.POST.get('confirm') != 'yes':
        return render(request, 'studio/task_delete.html', {
            'tasks': tasks, 'bulk': True, 'return_to': return_to, 'nav': 'teacher',
        })
    if action == 'publish' and any(not (t.statement or t.attachment or any(b.purpose == 'statement' for b in t.content_blocks.all())) for t in tasks):
        messages.error(request, 'В выборке есть пустые задания. Сначала добавьте условия.')
        return redirect(return_to)
    for t in tasks:
        if action == 'archive':
            archive(request.user, t)
        elif action in ('publish', 'draft'):
            t.published = action == 'publish'
            t.save(update_fields=['published'])
        elif action == 'difficulty' and level in dict(Task.LEVELS):
            t.difficulty = level
            t.save(update_fields=['difficulty'])
    messages.success(request, f'Обработано заданий: {len(tasks)}.')
    return redirect(return_to)


@staff_required
@transaction.atomic
def import_tasks(request):
    batch = None
    if request.method == 'POST' and request.POST.get('batch','').isdigit():
        batch = get_object_or_404(ImportBatch.objects.select_for_update(), pk=request.POST['batch'], user=request.user)
        if batch.committed_at:
            messages.info(request, 'Этот импорт уже выполнен.')
            return redirect('teacher_tasks')
        if not batch.errors and batch.rows:
            # Validate again at commit: referenced topics may have changed after preview.
            objects = []
            try:
                for row in batch.rows:
                    obj = Task(**row, published=False)
                    obj.full_clean()
                    objects.append(obj)
            except ValidationError as exc:
                messages.error(request, '; '.join(exc.messages))
            else:
                for obj in objects:
                    obj.save()
                batch.committed_at = timezone.now()
                batch.save(update_fields=['committed_at'])
                messages.success(request, f'Добавлено черновиков: {len(objects)}.')
                return redirect('teacher_tasks')
    elif request.method == 'POST':
        f = request.FILES.get('file')
        rows, errors = [], []
        if not f or f.size>2*1024*1024 or Path(f.name).suffix.lower() != '.csv':
            errors.append('Загрузите CSV в UTF-8 размером до 2 МБ.')
        else:
            try:
                content = f.read().decode('utf-8-sig')
                try:
                    dialect = csv.Sniffer().sniff(content[:10000], delimiters=',;\t')
                except csv.Error:
                    dialect = csv.excel
                reader = csv.DictReader(io.StringIO(content), dialect=dialect)
                if not {'number','title','answer'}.issubset(reader.fieldnames or []):
                    raise ValueError('Нужны столбцы number, title, answer.')
                topics = dict(Topic.objects.values_list('number','pk'))
                for n, row in enumerate(reader, 2):
                    if n>1001:
                        errors.append('Максимум 1000 заданий за один импорт.')
                        break
                    try:
                        data = {k: (row.get(k) or '').strip() for k in ('title','statement','answer','solution','tags','subtopic')}
                        data.update(topic_id=topics[int(row['number'])], difficulty=(row.get('difficulty') or 'easy').strip(), source=(row.get('source') or 'Импорт CSV').strip())
                        task = Task(**data, published=False)
                        task.full_clean()
                        rows.append(data)
                    except (ValueError, KeyError, ValidationError) as exc:
                        errors.append(f'Строка {n}: {exc}')
            except (UnicodeError, ValueError, csv.Error) as exc:
                errors.append(str(exc))
        batch = ImportBatch.objects.create(user=request.user, rows=rows, errors=errors)
    return render(request, 'platform/import.html', {'batch': batch, 'nav': 'teacher'})


@staff_required
def duplicates(request):
    from .services import normalize
    groups = {}
    for t in Task.objects.prefetch_related('content_blocks').select_related('topic'):
        content = [normalize(t.statement)]
        content += [json.dumps({k: normalize(str(v)) for k,v in b.snapshot().items()}, sort_keys=True) for b in t.content_blocks.all() if b.purpose=='statement']
        if not any(content):
            continue
        key = hashlib.sha256(json.dumps([t.topic_id, content], ensure_ascii=False).encode()).hexdigest()
        groups.setdefault(key, []).append(t)
    return render(request, 'platform/duplicates.html', {'groups': [v for v in groups.values() if len(v)>1], 'nav': 'teacher'})


def editor_path(user, path):
    if len(path)>240 or not path.startswith('/teacher/') or '?' in path:
        raise PermissionDenied
    try:
        match = resolve(path)
    except Resolver404:
        raise PermissionDenied
    allowed = {'task_create','task_editor','block_create','block_editor','course_create','course_editor','section_create','section_editor','lesson_create','lesson_editor','step_create','step_editor'}
    if match.url_name not in allowed:
        raise PermissionDenied
    k = match.kwargs
    if match.url_name in ('course_editor','section_create','section_editor'):
        get_object_or_404(editable_courses(user), pk=k.get('course_pk', k.get('pk')))
    if match.url_name in ('lesson_create','lesson_editor'):
        get_object_or_404(Section, pk=k['section_pk'], course__in=editable_courses(user))
    if match.url_name in ('step_create','step_editor'):
        get_object_or_404(Lesson, pk=k['lesson_pk'], section__course__in=editable_courses(user))
    return path


@staff_required
@require_http_methods(['GET','POST','DELETE'])
@transaction.atomic
def draft(request):
    path = editor_path(request.user, request.GET.get('path', ''))
    d = EditorDraft.objects.select_for_update().filter(user=request.user, path=path).first()
    if request.method == 'DELETE':
        if d:
            d.delete()
        return JsonResponse({'ok':True})
    if request.method == 'POST':
        if len(request.body)>250000:
            return JsonResponse({'error':'Черновик слишком большой'},status=400)
        try:
            payload=json.loads(request.body)
            data=payload['data']
            revision=int(payload['revision'])
            if not isinstance(data,dict) or len(data)>1000 or any(not isinstance(k,str) or not isinstance(v,list) or any(not isinstance(s,str) for s in v) for k,v in data.items()):
                raise ValueError
        except (ValueError,KeyError,TypeError):
            return JsonResponse({'error':'Некорректный черновик'},status=400)
        if revision != (d.revision if d else 0):
            return JsonResponse({'error':'Черновик изменён в другой вкладке. Перезагрузите страницу.'},status=409)
        data.pop('csrfmiddlewaretoken',None)
        if not d:
            d=EditorDraft(user=request.user,path=path)
        d.data=data
        d.revision=revision+1
        d.save()
    return JsonResponse({'data':d.data if d else {},'revision':d.revision if d else 0,'updated_at':d.updated_at.isoformat() if d else None})


@staff_required
@require_POST
@transaction.atomic
def reorder(request):
    kind=request.POST.get('kind')
    pk=request.POST.get('parent')
    if kind=='section':
        parent=get_object_or_404(editable_courses(request.user),pk=pk)
        qs,field=parent.sections.all(),'order'
    elif kind=='lesson':
        parent=get_object_or_404(Section,pk=pk,course__in=editable_courses(request.user))
        qs,field=parent.lessons.all(),'order'
    elif kind=='step':
        parent=get_object_or_404(Lesson,pk=pk,section__course__in=editable_courses(request.user))
        qs,field=parent.steps.all(),'position'
    else:
        raise Http404
    try:
        ids=[int(i) for i in request.POST.getlist('ids')]
    except ValueError:
        raise PermissionDenied
    rows={obj.pk:obj for obj in qs.select_for_update()}
    if len(ids)!=len(set(ids)) or set(ids)!=set(rows):
        return JsonResponse({'error':'Состав изменился. Обновите страницу.'},status=409)
    for n,pk in enumerate(ids,1):
        setattr(rows[pk],field,n)
        rows[pk].save(update_fields=[field])
    return JsonResponse({'ok':True})


@login_required
@require_http_methods(['GET','POST'])
@transaction.atomic
def video_position(request, pk):
    from .course_views import step_access
    from django.contrib.auth import get_user_model
    if request.method == 'POST':
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
    step=get_object_or_404(Step.objects.select_related('lesson__section__course'),pk=pk)
    course=step_access(request.user,step)
    block = None
    if request.GET.get('block'):
        if not request.GET['block'].isdigit() or not step.use_blocks:
            raise Http404
        block = get_object_or_404(step.content_blocks, pk=request.GET['block'], kind='video')
    elif step.kind != 'video':
        raise Http404
    position=VideoPosition.objects.filter(user=request.user,step=step,revision=step.revision).first()
    if request.method=='POST' and not can_edit(request.user,course):
        try:
            seconds=int(request.POST['seconds'])
            if not 0<=seconds<=86400:
                raise ValueError
        except (ValueError,KeyError):
            return JsonResponse({'error':'Некорректная позиция'},status=400)
        positions = dict(position.block_positions) if position else {}
        if block:
            positions[str(block.pk)] = seconds
        position,_=VideoPosition.objects.update_or_create(user=request.user,step=step,defaults={'revision':step.revision,'seconds':position.seconds if block and position else 0 if block else seconds, 'block_positions': positions})
    seconds = position.seconds if position else 0
    if block and position:
        first_video = step.content_blocks.filter(kind='video').first()
        seconds = position.block_positions.get(str(block.pk), position.seconds if first_video.pk == block.pk and step.kind == 'video' else 0)
    return JsonResponse({'seconds':seconds})
