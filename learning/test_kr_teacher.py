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
    def test_draft_edit_and_send(self, create, _):
        topic = Topic.objects.get(number=1)
        extra = [Task.objects.create(topic=topic, title=f'Задача {k}', statement=f'Условие {k}', answer=str(k), difficulty='hard',
                                     published=True, in_catalog=True) for k in range(2, 8)]
        self.client.force_login(self.teacher)
        self.assertContains(self.client.post('/teacher/kr/', {'subject': 'inf', 'title': 'x', 'variants': '1', 'duration': '45'}),
                            'Укажите, сколько задач')
        reply = self.client.post('/teacher/kr/', {'subject': 'inf', 'title': 'КР № 1', 'group': 'Пара 02', 'variants': '2',
                                                  'duration': '45', 'level': 'hard', 'n1': '2', 'src1': 'bank'})
        self.assertRedirects(reply, '/teacher/kr/draft/', fetch_redirect_response=False)
        draft = self.client.session['kr_draft']
        hard = {t.pk for t in extra}
        self.assertEqual([len(ids) for ids in draft['variants']], [2, 2])
        self.assertTrue({i for ids in draft['variants'] for i in ids} <= hard)  # only the asked difficulty
        page = self.client.get('/teacher/kr/draft/')
        self.assertContains(page, 'Вариант 2')
        self.assertContains(page, 'ответ: <b>')
        first = draft['variants'][0][0]
        self.client.post('/teacher/kr/draft/', {'action': 'swap', 'v': '0', 'i': '0'})
        swapped = self.client.session['kr_draft']['variants'][0]
        self.assertNotIn(first, swapped)
        self.assertEqual(len({i for ids in self.client.session['kr_draft']['variants'] for i in ids}), 4)  # still no repeats
        self.client.post('/teacher/kr/draft/', {'action': 'remove', 'v': '1', 'i': '1'})
        self.assertEqual(len(self.client.session['kr_draft']['variants'][1]), 1)
        own = Task.objects.get(title='Задача')
        self.client.post('/teacher/kr/draft/', {'action': 'add', 'v': '1', 'task': str(own.pk)})
        self.assertIn(own.pk, self.client.session['kr_draft']['variants'][1])
        self.client.post('/teacher/kr/draft/', {'action': 'add', 'v': '1', 'task': str(own.pk)})  # no doubles
        self.assertEqual(self.client.session['kr_draft']['variants'][1].count(own.pk), 1)
        exact = self.client.session['kr_draft']['variants']
        reply = self.client.post('/teacher/kr/draft/', {'action': 'send'})
        self.assertRedirects(reply, '/teacher/kr/?subject=inf&new=AB12CD', fetch_redirect_response=False)
        self.assertEqual(create.call_args.kwargs['exact'], exact)
        self.assertNotIn('kr_draft', self.client.session)

    @patch.object(kr_client, 'tests_of', return_value=[])
    def test_picked_tasks_and_picker(self, _):
        self.client.force_login(self.teacher)
        own = Task.objects.get(title='Задача')
        found = self.client.get(f'/teacher/kr/tasks/?subject=inf&q={own.pk}')
        self.assertContains(found, f'data-pick="{own.pk}"')
        self.assertContains(self.client.get('/teacher/kr/tasks/?subject=inf&number=1&q=Услов'), 'Условие')
        self.assertContains(self.client.get('/teacher/kr/tasks/?subject=inf&number=2'), 'Ничего не нашлось')
        self.client.post('/teacher/kr/', {'subject': 'inf', 'title': 'КР', 'variants': '1', 'duration': '45',
                                          'n1': '1', 'src1': 'pick', 'picked': str(own.pk)})
        self.assertEqual(self.client.session['kr_draft']['variants'], [[own.pk]])

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


@override_settings(KR_API_TOKEN='secret', KR_PUBLIC_URL='/kr')
class TeacherKrFollowUpTests(TestCase):
    """The next КР from weak numbers arrives prefilled; a КР runs again with a new code; links reach results and pupils."""

    def setUp(self):
        self.teacher = get_user_model().objects.create_user('olga', password='x', is_staff=True)
        self.client.force_login(self.teacher)

    @patch.object(kr_client, 'tests_of', return_value=[])
    def test_builder_prefilled_from_the_address(self, _):
        page = self.client.get('/teacher/kr/', {'subject': 'inf', 'title': 'КР № 3 · повторение', 'group': 'Пара 02', 'n2': '1', 'n8': '2'})
        rows = {r['number']: r['count'] for r in page.context['rows']}
        self.assertEqual((rows[2], rows[8], rows[1]), (1, 2, 0))
        self.assertEqual(page.context['form']['title'], 'КР № 3 · повторение')

    @patch.object(kr_client, 'open_again', return_value={'test': LISTED[0], 'code': 'ZZ99XX'})
    def test_run_again(self, again):
        reply = self.client.post('/teacher/kr/again/', {'test': '7'})
        self.assertRedirects(reply, '/teacher/kr/?new=ZZ99XX', fetch_redirect_response=False)
        self.assertEqual(again.call_args.args[1], 7)

    def test_links_to_results_and_pupils(self):
        for to in ('results:AB12CD', 'pupils'):
            target = urlparse(self.client.get(f'/teacher/kr/open/?to={to}')['Location'])
            data = signing.loads(parse_qs(target.query)['t'][0], key='secret', salt='kr-sso')
            self.assertEqual(data['to'], to)
