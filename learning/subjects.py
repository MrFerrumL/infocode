"""Subjects of the platform: informatics and physics share accounts, groups and the schedule; each has its own
catalogue, numbers, collections, variants, notes, courses and «Пройденные номера».

The current subject is chosen in the top bar (stored in the session) and set for each request by
SubjectMiddleware; models with a `subject` field are filtered by it in their default managers, and new
objects get it automatically. Outside a request (management commands, tests) it is informatics unless
`using('phys')` says otherwise.
"""
import contextvars
from contextlib import contextmanager
from django.db import models

SUBJECTS = {
    'inf': {'code': 'inf', 'title': 'Информатика', 'genitive': 'информатике', 'numbers': 27, 'icon': '</>'},
    'phys': {'code': 'phys', 'title': 'Физика', 'genitive': 'физике', 'numbers': 26, 'icon': '⚛'},
}
CHOICES = [(code, info['title']) for code, info in SUBJECTS.items()]
DEFAULT = 'inf'
SESSION_KEY = 'subject'
_current = contextvars.ContextVar('infocode_subject', default=DEFAULT)


def current():
    return _current.get()


def info(code=None):
    return SUBJECTS[code or current()]


@contextmanager
def using(code):
    token = _current.set(code if code in SUBJECTS else DEFAULT)
    try:
        yield
    finally:
        _current.reset(token)


def subject_field():
    return models.CharField('Предмет', max_length=8, choices=CHOICES, default=current, db_index=True)


class SubjectManager(models.Manager):
    """Default manager of subject-specific models: only the current subject."""
    def get_queryset(self):
        return super().get_queryset().filter(subject=current())


class SubjectMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        code = request.session.get(SESSION_KEY) if hasattr(request, 'session') else None
        with using(code if code in SUBJECTS else DEFAULT):
            request.subject = info()
            return self.get_response(request)
