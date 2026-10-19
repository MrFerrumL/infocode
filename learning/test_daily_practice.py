from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from learning.daily_practice import daily_tasks
from learning.models import Practice, Task, Topic
from learning.security import form_token
from learning.similar_models import TaskSimilarity


class DailyPracticeTests(TestCase):
    """«Тренировка дня»: similar to mistakes, weak numbers, new numbers; stable for the day, solved in place."""

    def setUp(self):
        self.user = get_user_model().objects.create_user('pupil')
        self.topics = {n: Topic.objects.create(number=n, title=f'Тема {n}') for n in (5, 8, 12)}
        self.tasks = {n: [Task.objects.create(topic=self.topics[n], title=f'{n}-{i}', statement='x', answer=str(i), difficulty='easy' if i < 3 else 'hard')
                          for i in range(6)] for n in self.topics}
        yesterday = timezone.now() - timedelta(days=1)
        wrong = self.tasks[8][0]
        self.near = self.tasks[8][4]
        TaskSimilarity.objects.create(task=wrong, other=self.near, score=.9, rank=1)
        for task, correct in ((wrong, False), (self.tasks[8][1], False), (self.tasks[8][2], True), (self.tasks[5][0], True)):
            practice = Practice.objects.create(user=self.user, task=task, answer='?', correct=correct)
            Practice.objects.filter(pk=practice.pk).update(created_at=yesterday)

    def test_picks_and_reasons(self):
        picked = daily_tasks(self.user)
        ids = [pk for pk, _ in picked]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids[0], self.near.pk)
        self.assertIn('неверный ответ', picked[0][1])
        self.assertTrue(any('Слабое место: № 8' in reason for _, reason in picked))
        self.assertTrue(any('Новый номер: № 12' in reason for _, reason in picked))
        self.assertNotIn(self.tasks[8][2].pk, ids)  # solved before
        self.assertNotIn(self.tasks[5][0].pk, ids)
        # Solving today does not reshuffle the set.
        Practice.objects.create(user=self.user, task=self.near, answer='4', correct=True)
        self.assertEqual(daily_tasks(self.user), picked)

    def test_page_and_dashboard(self):
        self.client.force_login(self.user)
        page = self.client.get('/practice/today/')
        self.assertContains(page, 'Тренировка дня')
        self.assertContains(page, f'id="task-{self.near.pk}"')
        reply = self.client.post('/practice/today/', {'task_id': self.near.pk, 'submit_token': form_token(self.user, f'task:{self.near.pk}'),
                                 f'task-{self.near.pk}-answer': '4'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertTrue(reply.json()['solved'])
        self.assertContains(self.client.get('/practice/today/'), 'Решено 1 из')
        self.assertContains(self.client.get('/'), 'Тренировка дня · 1 из')
