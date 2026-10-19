"""Switch between the informatics and physics platforms (top bar)."""
from django.http import Http404
from django.shortcuts import redirect
from django.urls import Resolver404, resolve
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from .subjects import SESSION_KEY, SUBJECTS


def landing(request, path):
    """Stay in the same section when it is a list page; detail pages belong to one subject, so go home."""
    if path and url_has_allowed_host_and_scheme(path, allowed_hosts={request.get_host()}) and path.startswith('/'):
        try:
            match = resolve(path.split('?', 1)[0])
        except Resolver404:
            match = None
        if match and not match.kwargs and match.url_name not in ('logout', 'switch_subject'):
            return path.split('?', 1)[0]
    return '/teacher/' if path.startswith('/teacher/') and request.user.is_staff else '/'


@require_POST
def switch(request):
    code = request.POST.get('subject', '')
    if code not in SUBJECTS:
        raise Http404
    request.session[SESSION_KEY] = code
    return redirect(landing(request, request.POST.get('next', '')))


def subject_context(request):
    from .subjects import info
    return {'subject': info(), 'subjects': list(SUBJECTS.values())}
