from django.contrib import admin
from .models import Topic, Task, StudyGroup, Variant, VariantItem, Section, Lesson, QuizQuestion, LessonFile, Practice, ExamAttempt, LessonProgress
from .models import TaskBlock, StudentAssignment, Course, Step

class ArchiveAdmin(admin.ModelAdmin):
    exclude = ['archived_at']
    def delete_model(self, request, obj):
        from .platform_views import archive
        archive(request.user, obj)
    def delete_queryset(self, request, queryset):
        from .platform_views import archive
        for obj in queryset:
            archive(request.user, obj)


class BlockInline(admin.StackedInline):
    can_delete = False
    model = TaskBlock
    extra = 0

admin.site.site_header = 'Инфокод · управление платформой'
admin.site.site_title = 'Инфокод'
admin.site.index_title = 'Контент и обучение'

@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ['number', 'title']

@admin.register(Task)
class TaskAdmin(ArchiveAdmin):
    list_display = ['id', 'title', 'topic', 'difficulty', 'published']
    list_filter = ['topic', 'difficulty', 'published']
    search_fields = ['title', 'statement', 'source']
    list_editable = ['difficulty', 'published']
    inlines = [BlockInline]

@admin.register(StudyGroup)
class GroupAdmin(admin.ModelAdmin):
    filter_horizontal = ['students']

class ItemInline(admin.TabularInline):
    model = VariantItem
    extra = 0
    fields = ['position', 'number', 'task']
    readonly_fields = fields
    can_delete = False
    def has_add_permission(self, request, obj=None):
        return False

@admin.register(Variant)
class VariantAdmin(admin.ModelAdmin):
    list_display = ['title', 'kind', 'group', 'duration', 'published']
    list_filter = ['kind', 'published', 'group']
    fields = ['title', 'kind', 'group', 'published', 'duration', 'creator', 'topics']
    readonly_fields = ['duration', 'creator', 'topics']
    inlines = [ItemInline]
    def has_add_permission(self, request):
        return False
    def has_delete_permission(self, request, obj=None):
        return False

class QuizInline(admin.StackedInline):
    model = QuizQuestion
    extra = 1

class FileInline(admin.TabularInline):
    model = LessonFile
    extra = 1

class StepInline(admin.StackedInline):
    can_delete = False
    model = Step
    extra = 0
    fields = ['position', 'title', 'kind', 'published']
    show_change_link = True

@admin.register(Lesson)
class LessonAdmin(ArchiveAdmin):
    list_display = ['title', 'section', 'order', 'published']
    list_filter = ['section', 'published']
    fields = ['section', 'title', 'order', 'minutes', 'published']
    inlines = [StepInline]
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(section__course__owner=request.user)
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'section' and not request.user.is_superuser:
            kwargs['queryset'] = Section.objects.filter(course__owner=request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for obj in instances:
            if obj.pk:
                obj.revision += 1
                obj.edit_version += 1
            obj.save()
        formset.save_m2m()

@admin.register(Section)
class SectionAdmin(ArchiveAdmin):
    list_display = ['title', 'course', 'order']
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(course__owner=request.user)
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'course' and not request.user.is_superuser:
            kwargs['queryset'] = Course.objects.filter(owner=request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

@admin.register(StudentAssignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ['teacher', 'student', 'created_at']
    list_filter = ['teacher']
    autocomplete_fields = ['teacher', 'student']
    def has_module_permission(self, request):
        return request.user.is_superuser
    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser
    def has_add_permission(self, request):
        return request.user.is_superuser
    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser
    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

@admin.register(Course)
class CourseAdmin(ArchiveAdmin):
    list_display = ['title', 'owner', 'published']
    list_filter = ['published']
    def get_queryset(self, request):
        qs=super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(owner=request.user)
    def get_readonly_fields(self, request, obj=None):
        return [] if request.user.is_superuser else ['owner']
    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            obj.owner = request.user
        super().save_model(request, obj, form, change)

@admin.register(Step)
class StepAdmin(ArchiveAdmin):
    list_display = ['title', 'lesson', 'kind', 'position', 'published']
    list_filter = ['kind', 'published']
    def change_view(self, request, object_id, form_url='', extra_context=None):
        from django.shortcuts import get_object_or_404, redirect
        obj = get_object_or_404(self.get_queryset(request), pk=object_id)
        return redirect('step_editor', lesson_pk=obj.lesson_id, pk=obj.pk)
    def get_queryset(self, request):
        qs=super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(lesson__section__course__owner=request.user)
    def save_model(self, request, obj, form, change):
        if change:
            obj.revision += 1
            obj.edit_version += 1
        super().save_model(request, obj, form, change)
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'lesson' and not request.user.is_superuser:
            kwargs['queryset'] = Lesson.objects.filter(section__course__owner=request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
