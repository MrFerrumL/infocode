"""Seasonal look of the site (SITE_SEASON): a light decoration over the usual style.

auto (default) follows the calendar:
  autumn    — 1 September … 14 October, 3 November … 30 November
  halloween — 15 October … 2 November
  newyear   — 1 December … 14 January
  winter    — 15 January … end of February
SITE_SEASON=autumn|halloween|winter|newyear forces one; none (or empty) means no default decoration.
Each visitor can switch the look in the top bar menu (static/season.js); the choice stays in the browser.
"""
from django.conf import settings
from django.utils import timezone

SEASONS = ('autumn', 'halloween', 'winter', 'newyear')


def season_for(day):
    month, d = day.month, day.day
    if (month == 10 and d >= 15) or (month == 11 and d <= 2):
        return 'halloween'
    if month == 12 or (month == 1 and d <= 14):
        return 'newyear'
    if month in (1, 2):
        return 'winter'
    if month in (9, 10, 11):
        return 'autumn'
    return ''


def current_season(today=None):
    value = (getattr(settings, 'SITE_SEASON', 'auto') or '').strip().lower()
    if value in SEASONS:
        return value
    if value == 'auto':
        return season_for(today or timezone.localdate())
    return ''
