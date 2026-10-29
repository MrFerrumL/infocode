"""Pages visible without signing in: landing, contacts, privacy policy and robots.txt."""
from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .views import dashboard


def verification():
    return {'google_site_verification': settings.GOOGLE_SITE_VERIFICATION}


def contact_details():
    return {'owner': settings.SITE_OWNER_NAME, 'email': settings.SITE_CONTACT_EMAIL, 'telegram': settings.SITE_CONTACT_TELEGRAM.lstrip('@')}


def home(request):
    if request.user.is_authenticated:
        return dashboard(request)
    return render(request, 'public/home.html', {'contact': contact_details(), **verification()})


@require_GET
def contacts(request):
    return render(request, 'public/contacts.html', {'contact': contact_details()})


@require_GET
def privacy(request):
    return render(request, 'public/privacy.html', {'contact': contact_details()})


@require_GET
def robots_txt(request):
    lines = ['User-agent: *', 'Allow: /$', 'Allow: /contacts/', 'Allow: /privacy/', 'Allow: /login/', 'Allow: /register/', 'Disallow: /']
    return HttpResponse('\n'.join(lines) + '\n', content_type='text/plain; charset=utf-8')
