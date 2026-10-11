import hashlib
import logging
import time
from contextvars import ContextVar
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone

active_actor = ContextVar('platform_actor', default=None)


class TemporaryPasswordMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not request.user.is_staff and request.path not in ('/password/', '/logout/'):
            from .models import StudentAccess
            if StudentAccess.objects.filter(user=request.user, must_change_password=True).exists():
                from django.shortcuts import redirect
                return redirect('password')
        return self.get_response(request)


class RequestLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.monotonic()
        response = self.get_response(request)
        if response.status_code >= 500:
            # Never record form payloads, passwords, URL queries, invite tokens or exception locals.
            logging.getLogger('platform').error('HTTP %s %s route=%s elapsed_ms=%d', response.status_code, request.method, getattr(getattr(request, 'resolver_match', None), 'url_name', 'unknown'), (time.monotonic()-started)*1000)
        return response


class LoginThrottleMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .models import LoginBucket
        if request.method == 'POST' and request.path in ('/login/', '/admin/login/', '/register/'):
            now = timezone.now()
            ip = request.META.get('REMOTE_ADDR', '')
            name = request.POST.get('username', '').strip().casefold()[:150]
            for key_text, limit in [(ip+':'+name, settings.LOGIN_RATE_LIMIT), (ip, settings.LOGIN_RATE_LIMIT*10)]:
                key = hashlib.sha256((settings.SECRET_KEY + key_text).encode()).hexdigest()
                with transaction.atomic():
                    bucket, _ = LoginBucket.objects.select_for_update().get_or_create(key=key, defaults={'started_at': now})
                    age = (now-bucket.started_at).total_seconds()
                    if age >= settings.LOGIN_RATE_WINDOW:
                        bucket.count, bucket.started_at = 0, now
                    if bucket.count >= limit:
                        return HttpResponse('Слишком много попыток входа. Попробуйте позже.', status=429, headers={'Retry-After': str(max(1, int(settings.LOGIN_RATE_WINDOW-age)))})
                    bucket.count += 1
                    bucket.save(update_fields=['count', 'started_at'])
        token = active_actor.set(request.user if request.user.is_authenticated else None)
        try:
            response = self.get_response(request)
            if request.method == 'POST' and response.status_code == 302 and request.user.is_authenticated and request.POST.get('action') != 'add_option':
                from .models import EditorDraft
                EditorDraft.objects.filter(user=request.user, path=request.path).delete()
            return response
        finally:
            active_actor.reset(token)
