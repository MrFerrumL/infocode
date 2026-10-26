import json
from django.test import TestCase, override_settings
from learning.models import Task, Topic


@override_settings(KR_API_TOKEN='secret-token')
class KrApiTests(TestCase):
    """The separate test platform composes variants from this bank through a token-protected API."""

    def setUp(self):
        topic = Topic.objects.create(number=8, title='Комбинаторика')
        self.tasks = [Task.objects.create(topic=topic, title=f't{i}', statement=f'Условие {i}', answer=str(i)) for i in range(6)]
        Task.objects.create(topic=topic, title='без ответа', statement='?', answer='', source_system='fipi')
        self.auth = {'HTTP_AUTHORIZATION': 'Bearer secret-token'}

    def test_token_is_required(self):
        self.assertEqual(self.client.get('/api/kr/numbers/').status_code, 401)
        self.assertEqual(self.client.get('/api/kr/numbers/', HTTP_AUTHORIZATION='Bearer wrong').status_code, 401)
        with self.settings(KR_API_TOKEN=''):
            self.assertEqual(self.client.get('/api/kr/numbers/', **self.auth).status_code, 401)

    def test_numbers_and_compose_without_repeats(self):
        data = self.client.get('/api/kr/numbers/', **self.auth).json()
        self.assertEqual(data['numbers'], [{'number': 8, 'count': 6}])  # the task without an answer is left out
        reply = self.client.post('/api/kr/compose/', json.dumps({'subject': 'inf', 'variants': 3, 'numbers': [{'number': 8, 'count': 2}]}),
                                 content_type='application/json', **self.auth).json()
        ids = [t['id'] for v in reply['variants'] for t in v]
        self.assertEqual(len(ids), 6)
        self.assertEqual(len(set(ids)), 6)
        self.assertEqual(reply['variants'][0][0]['blocks'][0]['kind'], 'text')
        too_many = self.client.post('/api/kr/compose/', json.dumps({'variants': 4, 'numbers': [{'number': 8, 'count': 2}]}),
                                    content_type='application/json', **self.auth)
        self.assertEqual(too_many.status_code, 400)
        self.assertIn('без повторов', too_many.json()['error'])

    def post(self, spec):
        return self.client.post('/api/kr/compose/', json.dumps(spec), content_type='application/json', **self.auth)

    def test_sources_level_and_exact_variants(self):
        Task.objects.filter(pk__in=[t.pk for t in self.tasks[:3]]).update(difficulty='hard')
        hard = {t.pk for t in self.tasks[:3]}
        reply = self.post({'subject': 'inf', 'variants': 3, 'level': 'hard', 'numbers': [{'number': 8, 'count': 1}]}).json()
        self.assertEqual({t['id'] for v in reply['variants'] for t in v}, hard)
        self.assertIn('уровня', self.post({'variants': 4, 'level': 'hard', 'numbers': [{'number': 8, 'count': 1}]}).json()['error'])
        # Chosen tasks: variants share them only when too few were chosen.
        chosen = [self.tasks[4].pk, self.tasks[5].pk]
        reply = self.post({'variants': 2, 'numbers': [{'number': 8, 'count': 1, 'source': 'pick', 'ids': chosen}]}).json()
        self.assertEqual(sorted(t['id'] for v in reply['variants'] for t in v), sorted(chosen))
        self.assertEqual(self.post({'variants': 1, 'numbers': [{'number': 8, 'count': 3, 'source': 'pick', 'ids': chosen}]}).status_code, 400)
        # Ready variants as they are, in their order.
        exact = [[self.tasks[2].pk, self.tasks[0].pk], [self.tasks[1].pk]]
        reply = self.post({'subject': 'inf', 'exact': exact}).json()
        self.assertEqual([[t['id'] for t in v] for v in reply['variants']], exact)
        self.assertEqual(self.post({'subject': 'inf', 'exact': [[999999]]}).status_code, 400)

    def test_generated_tasks_stay_out_of_the_catalogue(self):
        Topic.objects.create(number=13, title='Динамическое программирование')
        reply = self.post({'variants': 2, 'numbers': [{'number': 13, 'count': 3, 'source': 'generate'}]}).json()
        ids = [t['id'] for v in reply['variants'] for t in v]
        self.assertEqual(len(set(ids)), 6)
        made = Task.all_objects.filter(pk__in=ids)
        self.assertEqual(set(made.values_list('source_system', 'published', 'in_catalog')), {('infocode-gen', True, False)})
        self.assertIn('нет генератора', self.post({'variants': 1, 'numbers': [{'number': 8, 'count': 1, 'source': 'generate'}]}).json()['error'])
