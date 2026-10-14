def platform_context(request):
    if not request.user.is_authenticated:
        return {}
    from .models import Notification
    route = request.resolver_match.url_name if request.resolver_match else ''
    teacher = request.user.is_staff and (request.path.startswith('/teacher/') or route in {'assignment_detail', 'result', 'print_variant', 'schedule_detail'})
    categories = {
        'home': {'teacher'},
        'homework': {'teacher_homework', 'homework_create'},
        'schedule': {'teacher_schedule', 'schedule_create', 'schedule_edit', 'schedule_detail'},
        'notebooks': {'teacher_notebooks', 'notebook_create', 'notebook_editor', 'notebook_delete', 'notebook_detail'},
        'courses': {'teacher_courses', 'course_create', 'course_editor', 'course_report', 'section_create', 'section_editor', 'lesson_create', 'lesson_editor', 'step_create', 'step_editor'},
        'tasks': {'teacher_tasks', 'task_create', 'task_editor', 'task_delete', 'bulk_tasks', 'block_create', 'block_editor', 'import_tasks', 'duplicates'},
        'works': {'journal', 'assignment_create', 'assignment_detail', 'review_homework', 'review_queue', 'variant_create', 'result', 'print_variant'},
        'students': {'student_account_create', 'student_password_reset', 'teacher_students', 'teacher_student', 'analytics', 'teacher_groups'},
    }
    return {
        'unread_notifications': Notification.objects.filter(user=request.user, read_at=None).count(),
        'teacher_workspace': teacher,
        'workspace_section': next((key for key, names in categories.items() if route in names), 'more'),
        'current_route': route,
    }
