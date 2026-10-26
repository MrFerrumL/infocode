"""Teachers build КР for the separate test platform right here, in their workspace.

The test platform (KR_INTERNAL_URL inside the Docker network) answers with the КР code; teachers then open its live
page through a short signed link (KR_PUBLIC_URL/sso/), so they need no second password there.
Both sides share KR_API_TOKEN: it guards the platform's API and signs the links.
"""
import json
import urllib.error
import urllib.parse
import urllib.request
from django.conf import settings
from django.core import signing

SALT = 'kr-sso'  # the same salt on the test platform (kr/integration.py)


class KrError(Exception):
    pass


def enabled():
    return bool(getattr(settings, 'KR_API_TOKEN', ''))


def _call(path, data=None, timeout=20):
    request = urllib.request.Request(settings.KR_INTERNAL_URL.rstrip('/') + '/' + path.lstrip('/'),
                                     data=json.dumps(data).encode() if data is not None else None,
                                     headers={'Authorization': f'Bearer {settings.KR_API_TOKEN}', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read()).get('error', '')
        except ValueError:
            message = ''
        raise KrError(message or f'Платформа КР ответила ошибкой {exc.code}.') from exc
    except (OSError, ValueError) as exc:
        raise KrError('Платформа КР сейчас недоступна. Попробуйте через минуту.') from exc


def teacher(user):
    return {'username': user.username, 'name': user.get_full_name() or user.username}


def tests_of(user):
    return _call('api/tests/?' + urllib.parse.urlencode({'teacher': user.username}))['tests']


def create(user, title, group, subject, plan, variants, duration, level=None, exact=None):
    """Build a КР on the test platform and open it; returns {'test': ..., 'code': ...}.
    `exact` = [[task ids], …] sends variants already put together here (then `plan` is not used)."""
    return _call('api/tests/', {'teacher': teacher(user), 'title': title, 'group': group, 'subject': subject, 'plan': plan,
                                'variants': variants, 'duration': duration, 'level': level, 'exact': exact or []}, timeout=180)


def link(user, to=''):
    """A two-minute link that signs the teacher in on the test platform and opens `to` (session:<code>, test:<id>)."""
    token = signing.dumps({'u': user.username, 'n': teacher(user)['name'], 'to': to}, key=settings.KR_API_TOKEN, salt=SALT)
    return f'{settings.KR_PUBLIC_URL.rstrip("/")}/sso/?t={token}'


def pupils_url():
    return settings.KR_PUBLIC_URL.rstrip('/') + '/'
