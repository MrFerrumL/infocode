from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase, override_settings
from . import kr_client
from .models import Task, Topic

LISTED = [{'id': 7, 'title': 'КР № 1', 'group': 'Пара 02', 'subject': 'inf', 'duration': 45, 'variants': 3, 'tasks': 5,
           'created_at': '2026-10-25T10:00:00+03:00',
           'sessions': [{'code': 'AB12CD', 'closed': False, 'created_at': '2026-10-25T10:01:00+03:00', 'join': '/kr/s/AB12CD/',
                         'waiting': 2, 'writing': 1, 'finished': 0}]}]


@override_settings(KR_API_TOKEN='secret', KR_PUBLIC_URL='/kr')
class TeacherKrTests(TestCase):
    """The teacher builds a КР for the separate platform in the workspace, gets the code and opens its live page."""

    def setUp(self):
        users = get_user_model()
        self.teacher = users.objects.create_user('olga', password='x', first_name='Ольга', is_staff=True)
        self.pupil = users.objects.create_user('pupil', password='x')
        topic = Topic.objects.create(number=1, title='Анализ моделей')
        Task.objects.create(topic=topic, title='Задача', statement='Условие', answer='12', published=True, in_catalog=True)

    def test_only_teachers(self):
        self.client.force_login(self.pupil)
        self.assertEqual(self.client.get('/teacher/kr/').status_code, 302)
        self.assertEqual(self.client.get('/teacher/kr/open/').status_code, 302)

    @patch.object(kr_client, 'tests_of', return_value=LISTED)
    def test_page_lists_codes_and_bank_counts(self, _):
        self.client.force_login(self.teacher)
        page = self.client.get('/teacher/kr/')
        self.assertContains(page, 'AB12CD')
        self.assertContains(page, 'http://testserver/kr/s/AB12CD/')
        self.assertContains(page, 'в банке 1')
        self.assertContains(page, '?to=session:AB12CD')

    @patch.object(kr_client, 'tests_of', return_value=[])
    @patch.object(kr_client, 'create', return_value={'test': LISTED[0], 'code': 'AB12CD'})
    def test_build_sends_the_plan(self, create, _):
        self.client.force_login(self.teacher)
        reply = self.client.post('/teacher/kr/', {'subject': 'inf', 'title': 'КР № 1', 'group': 'Пара 02', 'variants': '3',
                                                  'duration': '45', 'n1': '2', 'n2': '0'})
        self.assertRedirects(reply, '/teacher/kr/?subject=inf&new=AB12CD', fetch_redirect_response=False)
        args = create.call_args.args
        self.assertEqual((args[1], args[2], args[3], args[4], args[5], args[6]), ('КР № 1', 'Пара 02', 'inf', [{'number': 1, 'count': 2}], 3, 45))
        self.assertContains(self.client.post('/teacher/kr/', {'subject': 'inf', 'title': 'x', 'variants': '1', 'duration': '45'}),
                            'Укажите, сколько задач')

    @patch.object(kr_client, 'tests_of', side_effect=kr_client.KrError('Платформа КР сейчас недоступна. Попробуйте через минуту.'))
    def test_platform_down_is_explained(self, _):
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get('/teacher/kr/'), 'Платформа КР сейчас недоступна')

    def test_open_signs_a_short_link(self):
        self.client.force_login(self.teacher)
        reply = self.client.get('/teacher/kr/open/?to=session:AB12CD')
        target = urlparse(reply['Location'])
        self.assertEqual(target.path, '/kr/sso/')
        data = signing.loads(parse_qs(target.query)['t'][0], key='secret', salt='kr-sso', max_age=120)
        self.assertEqual(data, {'u': 'olga', 'n': 'Ольга', 'to': 'session:AB12CD'})
        odd = urlparse(self.client.get('/teacher/kr/open/?to=../../admin').get('Location'))
        self.assertEqual(signing.loads(parse_qs(odd.query)['t'][0], key='secret', salt='kr-sso')['to'], '')
