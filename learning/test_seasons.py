from datetime import date
from django.test import TestCase, override_settings
from learning.seasons import current_season, season_for


class SeasonTests(TestCase):
    """Halloween, winter and New Year decorations follow the calendar unless SITE_SEASON forces one."""

    def test_calendar(self):
        self.assertEqual(season_for(date(2026, 10, 18)), 'halloween')
        self.assertEqual(season_for(date(2026, 11, 2)), 'halloween')
        self.assertEqual(season_for(date(2026, 11, 3)), 'winter')
        self.assertEqual(season_for(date(2026, 12, 1)), 'newyear')
        self.assertEqual(season_for(date(2027, 1, 14)), 'newyear')
        self.assertEqual(season_for(date(2027, 1, 15)), 'winter')
        self.assertEqual(season_for(date(2027, 3, 1)), '')
        self.assertEqual(season_for(date(2027, 10, 14)), '')

    def test_setting_and_page(self):
        with self.settings(SITE_SEASON='newyear'):
            self.assertEqual(current_season(), 'newyear')
            page = self.client.get('/')
            self.assertContains(page, 'season-newyear')
            self.assertContains(page, 'season.js')
        with self.settings(SITE_SEASON='none'):
            self.assertEqual(current_season(), '')
            self.assertNotContains(self.client.get('/'), 'season.js')
        with self.settings(SITE_SEASON='auto'):
            self.assertEqual(current_season(date(2026, 10, 31)), 'halloween')
