import gzip
import json
import tempfile
from pathlib import Path
from django.contrib.auth import get_user_model
from django.test import TestCase
from learning.models import Practice, Task, Topic
from learning.security import form_token
from learning.similar_models import TaskSimilarity
from learning.similar_tasks import similar_to, sync


class SimilarTasksTests(TestCase):
    """The "Похожие" button: neural neighbours of the same number, unsolved ones first, solved in place."""

    def setUp(self):
        topic = Topic.objects.create(number=8, title='Комбинаторика')
        make = lambda ident, **kw: Task.objects.create(topic=topic, title=f't{ident}', statement='x', answer=str(ident), source_system='kompege', external_id=ident, **kw)
        self.a, self.b, self.c, self.d = make(1), make(2), make(3), make(4, difficulty='hard')
        self.own = Task.objects.create(topic=topic, title='own', statement='x', answer='5')
        self.user = get_user_model().objects.create_user('pupil')
        self.client.force_login(self.user)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'similar.json.gz'
        self.path.write_bytes(gzip.compress(json.dumps({'model': 'test', 'neighbours': {
            'kompege:8:1': [['kompege:8:2', 0.9], ['kompege:8:3', 0.8], ['kompege:8:99', 0.7]]}}).encode()))

    def test_sync_and_order_for_the_pupil(self):
        self.assertEqual(sync(self.path), (1, 2))
        self.assertEqual(similar_to(self.a, self.user), ([self.b.pk, self.c.pk], 'model'))
        Practice.objects.create(user=self.user, task=self.b, answer='2', correct=True)
        self.assertEqual(similar_to(self.a, self.user)[0], [self.c.pk, self.b.pk])
        # A task without neighbours falls back to the same number and difficulty.
        ids, how = similar_to(self.own, self.user)
        self.assertEqual(how, 'number')
        self.assertNotIn(self.own.pk, ids)
        self.assertNotIn(self.d.pk, ids)

    def test_button_loads_cards_that_are_solved_in_place(self):
        sync(self.path)
        catalog = self.client.get('/catalog/?number=8')
        self.assertContains(catalog, f'data-similar="/catalog/{self.a.pk}/similar/"')
        page = self.client.get(f'/catalog/{self.a.pk}/similar/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertContains(page, f'id="task-{self.b.pk}"')
        self.assertContains(page, 'нейросетью')
        reply = self.client.post(f'/catalog/{self.a.pk}/similar/', {'task_id': self.c.pk, 'submit_token': form_token(self.user, f'task:{self.c.pk}'),
                                 f'task-{self.c.pk}-answer': '3'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertTrue(reply.json()['solved'])
        self.client.logout()
        self.assertEqual(self.client.get(f'/catalog/{self.a.pk}/similar/').status_code, 302)
