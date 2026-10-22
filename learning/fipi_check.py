"""Answers to FIPI open-bank tasks are checked by FIPI itself (the owner's decision), as its «Ответить» button does.

FIPI does not publish answers. The site opens a session on the bank page (cookie PHPSESSID) and posts the
pupil's answer to solve.php: "3" (or "1") means right, "2" — wrong; anything else means FIPI could not check.
The first right answer is saved in the task, after which the task is checked locally like any other.
"""
import hashlib
import http.cookiejar
import logging
import urllib.parse
import urllib.request
from django.core.cache import cache

BANK = 'https://ege.fipi.ru/bank/'
PROJECT = 'B9ACA5BBB2E19E434CD6BEC25284C67F'  # ЕГЭ · Информатика и ИКТ
TIMEOUT = 8
logger = logging.getLogger('platform')


class Unavailable(Exception):
    """FIPI did not answer or did not recognise the request."""


def _opener():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [('User-Agent', 'Mozilla/5.0 (Infocode; teacher platform)')]
    return opener, jar


def _session(refresh=False):
    cookie = None if refresh else cache.get('fipi-session')
    if cookie:
        return cookie
    opener, jar = _opener()
    try:
        opener.open(f'{BANK}index.php?proj={PROJECT}', timeout=TIMEOUT).read(1024)
    except OSError as exc:
        raise Unavailable(str(exc)) from exc
    cookie = next((c.value for c in jar if c.name == 'PHPSESSID'), '')
    if not cookie:
        raise Unavailable('no session')
    cache.set('fipi-session', cookie, 20 * 60)
    return cookie


def _post(guid, answer, cookie):
    data = urllib.parse.urlencode({'guid': guid, 'answer': answer, 'ajax': '1', 'proj': PROJECT}).encode()
    request = urllib.request.Request(f'{BANK}solve.php', data=data, headers={
        'Cookie': f'PHPSESSID={cookie}', 'User-Agent': 'Mozilla/5.0 (Infocode; teacher platform)',
        'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read(64).decode('cp1251', 'replace').strip()
    except OSError as exc:
        raise Unavailable(str(exc)) from exc


def check(guid, answer):
    """True / False for a right / wrong answer; raises Unavailable when FIPI cannot check now."""
    answer = ' '.join(str(answer).split())
    key = 'fipi-answer:' + hashlib.sha256(f'{guid}:{answer.casefold()}'.encode()).hexdigest()
    known = cache.get(key)
    if known is not None:
        return known
    reply = _post(guid, answer, _session())
    if reply not in ('1', '2', '3'):
        reply = _post(guid, answer, _session(refresh=True))  # the session may have expired
    if reply not in ('1', '2', '3'):
        logger.warning('FIPI check failed for %s: %r', guid, reply[:40])
        raise Unavailable(reply[:40])
    right = reply in ('1', '3')
    cache.set(key, right, 24 * 3600)
    return right
