import re
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from .models import Task, Topic


def unique(items):
    """Each title shows up twice on a card (heading and condition): keep the first appearance, in order."""
    return list(dict.fromkeys(items))


def order_of(page):
    """The task titles in the order they appear on the page."""
    return unique(re.findall(r'(КОМПЕГЭ №\d+|К\. Поляков 14\.\d+|Своя задача)', page.content.decode()))


class BankSortTests(TestCase):
    """КЕГЭ and K. Polyakov can be listed in their own numbering, from the first task or from the last."""

    def setUp(self):
        self.teacher = get_user_model().objects.create_user('olga', password='x', is_staff=True)
        topic = Topic.objects.create(number=14, title='Системы счисления')
        make = lambda title, system, ext: Task.objects.create(topic=topic, title=title, statement=title, answer='1', published=True,  # noqa: E731
                                                              in_catalog=True, source_system=system, external_id=ext)
        for ext in (30, 10, 20):
            make(f'КОМПЕГЭ №{ext} · задание 14', 'kompege', ext)
        for ext in (3, 1, 2):
            make(f'К. Поляков 14.{ext}', 'polyakov', ext)
        make('Своя задача', '', None)
        self.client.force_login(self.teacher)

    def test_catalog_kompege_from_the_first_and_from_the_last(self):
        up = order_of(self.client.get('/catalog/', {'sort': 'kompege_asc'}))
        self.assertEqual(up, ['КОМПЕГЭ №10', 'КОМПЕГЭ №20', 'КОМПЕГЭ №30'])  # only that bank, in its numbering
        down = order_of(self.client.get('/catalog/', {'sort': 'kompege_desc'}))
        self.assertEqual(down, ['КОМПЕГЭ №30', 'КОМПЕГЭ №20', 'КОМПЕГЭ №10'])

    def test_catalog_polyakov_from_the_first_and_from_the_last(self):
        self.assertEqual(order_of(self.client.get('/catalog/', {'sort': 'polyakov_asc'})),
                         ['К. Поляков 14.1', 'К. Поляков 14.2', 'К. Поляков 14.3'])
        self.assertEqual(order_of(self.client.get('/catalog/', {'sort': 'polyakov_desc'})),
                         ['К. Поляков 14.3', 'К. Поляков 14.2', 'К. Поляков 14.1'])

    def test_a_bank_sort_overrides_another_site_filter_and_keeps_the_other_filters(self):
        page = self.client.get('/catalog/', {'sort': 'polyakov_asc', 'site': 'kompege', 'number': '14'})
        self.assertEqual(len(order_of(page)), 3)
        self.assertTrue(all('Поляков' in t for t in order_of(page)))
        self.assertContains(page, '<option value="polyakov_asc" selected>')
        self.assertContains(page, 'К. Поляков: с последней задачи')

    def test_the_kr_picker_has_the_same_orders(self):
        for sort, expected in [('kompege_asc', [10, 20, 30]), ('kompege_desc', [30, 20, 10])]:
            page = self.client.get('/teacher/kr/tasks/', {'subject': 'inf', 'sort': sort})
            self.assertEqual(unique(re.findall(r'КОМПЕГЭ №(\d+)', page.content.decode())), [str(n) for n in expected], sort)
        page = self.client.get('/teacher/kr/tasks/', {'subject': 'inf', 'sort': 'polyakov_desc'})
        self.assertEqual(unique(re.findall(r'К\. Поляков 14\.(\d+)', page.content.decode())), ['3', '2', '1'])
        self.assertNotContains(page, 'КОМПЕГЭ')
        # without a bank sort the picker still shows everything (newest first)
        self.assertEqual(len(order_of(self.client.get('/teacher/kr/tasks/', {'subject': 'inf'}))), 7)

    @override_settings(KR_API_TOKEN='secret', KR_PUBLIC_URL='/kr')
    def test_the_picker_and_the_draft_offer_the_order_menu(self):
        from unittest.mock import patch
        from . import kr_client
        with patch.object(kr_client, 'tests_of', return_value=[]):
            self.assertContains(self.client.get('/teacher/kr/'), 'data-filter-sort')
            self.assertContains(self.client.get('/teacher/kr/'), 'КЕГЭ: с первой задачи')
        session = self.client.session
        session['kr_draft'] = {'subject': 'inf', 'title': 'КР', 'group': '', 'duration': 45, 'level': '', 'source': '', 'variants': [[]]}
        session.save()
        self.assertContains(self.client.get('/teacher/kr/draft/'), 'К. Поляков: с последней задачи')
