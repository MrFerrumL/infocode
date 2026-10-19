from django.contrib.auth import get_user_model
from django.test import TestCase
from learning.coverage_models import GroupTopicMark
from learning.models import StudyGroup


class CoverageTests(TestCase):
    """«Пройденные номера»: the teacher marks numbers per group; pupils see all their teacher's groups."""

    def setUp(self):
        users = get_user_model().objects
        self.teacher = users.create_user('teacher', is_staff=True)
        self.other_teacher = users.create_user('other', is_staff=True)
        self.pupil, self.mate, self.stranger = users.create_user('pupil'), users.create_user('mate'), users.create_user('stranger')
        self.pair = StudyGroup.objects.create(title='Пара 01: Ольговой Р. / Мишхожева Э.', teacher=self.teacher)
        self.pair.students.add(self.pupil)
        self.second = StudyGroup.objects.create(title='Пара 02: Десятерик Д. / Мартенс К.', teacher=self.teacher)
        self.second.students.add(self.mate)
        self.foreign = StudyGroup.objects.create(title='Чужая группа', teacher=self.other_teacher)
        self.foreign.students.add(self.stranger)

    def mark(self, cell, tool='check', ajax=True):
        extra = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'} if ajax else {}
        return self.client.post('/teacher/progress/mark/', {'cell': cell, 'tool': tool}, **extra)

    def test_teacher_marks_cells(self):
        self.client.force_login(self.teacher)
        page = self.client.get('/teacher/progress/')
        self.assertContains(page, 'Ольговой Р. / Мишхожева Э.')
        self.assertContains(page, 'введение')
        self.assertNotContains(page, 'Чужая группа')
        self.assertEqual(self.mark(f'{self.pair.pk}:0').json(), {'done': True, 'color': '', 'row_done': 1})
        self.assertEqual(self.mark(f'{self.pair.pk}:7', 'red').json()['color'], 'red')
        self.assertEqual(self.mark(f'{self.pair.pk}:7').json(), {'done': True, 'color': 'red', 'row_done': 2})
        self.assertEqual(self.mark(f'{self.pair.pk}:7', 'red').json()['color'], '')  # same colour again removes it
        self.assertEqual(self.mark(f'{self.pair.pk}:0').json()['done'], False)
        self.assertRedirects(self.mark(f'{self.pair.pk}:5', 'yellow', ajax=False), f'/teacher/progress/?tool=yellow#group-{self.pair.pk}', fetch_redirect_response=False)
        self.assertEqual(self.mark(f'{self.foreign.pk}:1').status_code, 404)
        self.assertEqual(self.mark(f'{self.pair.pk}:28').status_code, 400)

    def test_pupils_see_all_groups_of_their_teacher(self):
        GroupTopicMark.objects.create(group=self.second, column=3, done=True, color='yellow')
        self.client.force_login(self.pupil)
        page = self.client.get('/progress/numbers/')
        content = page.content.decode()
        self.assertIn('твоя группа', content)
        self.assertIn('Десятерик Д. / Мартенс К.', content)
        self.assertNotIn('Чужая группа', content)
        self.assertLess(content.index('Ольговой Р.'), content.index('Десятерик Д.'))
        self.assertIn('cov-cell yellow', content)
        self.assertNotIn('name="cell"', content)  # read-only
        self.assertNotEqual(self.mark(f'{self.pair.pk}:1').status_code, 200)
        self.assertFalse(GroupTopicMark.objects.filter(group=self.pair).exists())
