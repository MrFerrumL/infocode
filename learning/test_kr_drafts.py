import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from . import kr_client
from .models import KrDraft, Task, Topic


def make_bank(numbers=(5, 8, 17), per_number=40, source=''):
    tasks = {}
    for n in numbers:
        topic = Topic.objects.create(number=n, title=f'№ {n}')
        tasks[n] = [Task.objects.create(topic=topic, title=f't{n}-{k}', statement=f'Условие {n}-{k}', answer=str(k + 1), published=True,
                                        in_catalog=True, difficulty='ege', exam_source=source) for k in range(per_number)]
    return tasks


@override_settings(KR_API_TOKEN='secret', KR_PUBLIC_URL='/kr')
class SavedDraftTests(TestCase):
    """A КР draft is kept in the database: it is listed on the КР page, edited later and can be deleted."""

    def setUp(self):
        users = get_user_model()
        self.teacher = users.objects.create_user('olga', password='x', is_staff=True)
        self.other = users.objects.create_user('ivan', password='x', is_staff=True)
        make_bank()
        self.client.force_login(self.teacher)

    def build(self, title='КР № 1'):
        with patch.object(kr_client, 'tests_of', return_value=[]):
            return self.client.post('/teacher/kr/', {'subject': 'inf', 'title': title, 'variants': '3', 'duration': '45',
                                                     'n5': '2', 'src5': 'bank', 'n17': '1', 'src17': 'bank'})

    def test_the_builder_saves_the_draft_and_edits_follow(self):
        self.assertRedirects(self.build(), '/teacher/kr/draft/', fetch_redirect_response=False)
        row = KrDraft.objects.get()
        self.assertEqual((row.owner, row.title, len(row.data['variants'])), (self.teacher, 'КР № 1', 3))
        self.assertEqual(self.client.session['kr_draft']['saved_id'], row.pk)
        first = row.data['variants'][0][0]
        self.client.post('/teacher/kr/draft/', {'action': 'remove', 'v': '0', 'i': '0'})
        row.refresh_from_db()
        self.assertEqual(len(row.data['variants'][0]), 2)
        self.assertNotIn(first, row.data['variants'][0])
        self.assertEqual(KrDraft.objects.count(), 1)  # the same row, not a new one

    def test_list_open_and_delete(self):
        self.build('Первая')
        self.build('Вторая')
        self.assertEqual(KrDraft.objects.count(), 2)
        with patch.object(kr_client, 'tests_of', return_value=[]):
            page = self.client.get('/teacher/kr/')
        self.assertContains(page, 'Мои черновики КР')
        self.assertContains(page, 'Первая')
        self.assertContains(page, 'по 3 задач')
        first = KrDraft.objects.get(title='Первая')
        self.assertRedirects(self.client.post(f'/teacher/kr/drafts/{first.pk}/open/'), '/teacher/kr/draft/', fetch_redirect_response=False)
        self.assertEqual(self.client.session['kr_draft']['title'], 'Первая')
        self.assertContains(self.client.get('/teacher/kr/draft/'), 'Первая')
        self.client.post(f'/teacher/kr/drafts/{first.pk}/delete/')
        self.assertFalse(KrDraft.objects.filter(title='Первая').exists())
        self.assertNotIn('kr_draft', self.client.session)  # it was the open one

    def test_discard_and_send_forget_the_saved_copy(self):
        self.build()
        self.client.post('/teacher/kr/draft/', {'action': 'discard'})
        self.assertEqual(KrDraft.objects.count(), 0)
        self.build()
        with patch.object(kr_client, 'create', return_value={'test': {'title': 'КР № 1'}, 'code': 'AB12CD'}):
            self.client.post('/teacher/kr/draft/', {'action': 'send'})
        self.assertEqual(KrDraft.objects.count(), 0)

    def test_title_group_and_time_can_be_changed(self):
        self.build()
        self.client.post('/teacher/kr/draft/', {'action': 'meta', 'title': 'Новое название', 'group': 'Пара 03', 'duration': '120'})
        row = KrDraft.objects.get()
        self.assertEqual((row.title, row.data['group'], row.data['duration']), ('Новое название', 'Пара 03', 120))
        self.assertContains(self.client.get('/teacher/kr/draft/'), 'value="Пара 03"')
        self.client.post('/teacher/kr/draft/', {'action': 'meta', 'title': '', 'group': '', 'duration': '9999'})
        row.refresh_from_db()
        self.assertEqual((row.title, row.data['group'], row.data['duration']), ('Новое название', '', 300))

    def test_drafts_are_private_to_their_teacher(self):
        self.build()
        row = KrDraft.objects.get()
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(f'/teacher/kr/drafts/{row.pk}/open/').status_code, 404)
        self.assertEqual(self.client.post(f'/teacher/kr/drafts/{row.pk}/delete/').status_code, 404)
        with patch.object(kr_client, 'tests_of', return_value=[]):
            self.assertNotContains(self.client.get('/teacher/kr/'), 'КР № 1')
        self.assertEqual(KrDraft.objects.count(), 1)


class MakeDraftsCommandTests(TestCase):
    def setUp(self):
        self.teacher = get_user_model().objects.create_user('olga', password='x', is_staff=True)
        # №17 has only 10 official tasks, so the second draft has to take any source for it.
        self.bank = make_bank((5, 8), 60, source='main')
        topic = Topic.objects.create(number=17, title='№ 17')
        for k in range(10):
            Task.objects.create(topic=topic, title=f'o{k}', statement=f'Официальная {k}', answer='1', published=True, in_catalog=True,
                                difficulty='ege', exam_source='demo')
        for k in range(30):
            Task.objects.create(topic=topic, title=f'a{k}', statement=f'Авторская {k}', answer='1', published=True, in_catalog=True,
                                difficulty='hard')

    def run_plan(self, plan, *args):
        out = StringIO()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'plan.json'
            path.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
            call_command('make_kr_drafts', '--user', 'olga', '--plan', str(path), *args, stdout=out)
        return out.getvalue()

    def test_drafts_have_variants_and_no_repeats_between_them(self):
        plan = [{'title': 'КР А', 'numbers': {'5': 3, '17': 2}, 'variants': 4},
                {'title': 'КР Б', 'numbers': {'5': 2, '8': 2, '17': 2}, 'variants': 4, 'duration': 30}]
        report = self.run_plan(plan)
        self.assertIn('Drafts made: 2', report)
        a, b = (KrDraft.objects.get(title=t) for t in ('КР А', 'КР Б'))
        self.assertEqual([len(v) for v in a.data['variants']], [5] * 4)
        self.assertEqual([len(v) for v in b.data['variants']], [6] * 4)
        self.assertEqual(b.data['duration'], 30)
        ids_a = {i for v in a.data['variants'] for i in v}
        ids_b = {i for v in b.data['variants'] for i in v}
        self.assertEqual((len(ids_a), len(ids_b)), (20, 24))  # nothing repeats inside a draft
        self.assertFalse(ids_a & ids_b)  # nor between the drafts
        numbers = dict(Task.objects.values_list('pk', 'topic__number'))
        self.assertEqual(sorted(numbers[i] for i in a.data['variants'][0]), [5, 5, 5, 17, 17])
        self.assertEqual(a.owner, self.teacher)

    def test_a_number_short_of_official_tasks_gets_the_rest_from_the_bank(self):
        report = self.run_plan([{'title': 'КР В', 'numbers': {'5': 1, '17': 4}, 'variants': 4}])  # №17: 16 needed, 10 official
        self.assertIn('6 of 20 are not from the source', report)
        row = KrDraft.objects.get(title='КР В')
        self.assertEqual(row.data['source'], 'official')
        ids = [i for v in row.data['variants'] for i in v]
        self.assertEqual((len(ids), len(set(ids))), (20, 20))
        sources = dict(Task.objects.filter(pk__in=ids).values_list('pk', 'exam_source'))
        self.assertEqual(sum(sources[i] == 'demo' for i in ids), 10)  # all ten official №17 were used
        self.assertTrue(all(sources[i] == 'main' for v in row.data['variants'] for i in v[:1]))  # №5 stays official

    def test_existing_titles_are_left_alone_unless_replace(self):
        plan = [{'title': 'КР А', 'numbers': {'5': 1}, 'variants': 2}]
        self.run_plan(plan)
        row = KrDraft.objects.get()
        row.data['variants'][0] = []  # the teacher emptied a variant by hand
        row.save()
        self.assertIn('already there', self.run_plan(plan))
        row.refresh_from_db()
        self.assertEqual(row.data['variants'][0], [])
        self.run_plan(plan, '--replace')
        self.assertEqual(KrDraft.objects.count(), 1)
        self.assertEqual(len(KrDraft.objects.get().data['variants'][0]), 1)

    def test_dry_run_saves_nothing(self):
        self.run_plan([{'title': 'КР А', 'numbers': {'5': 1}, 'variants': 2}], '--dry-run')
        self.assertEqual(KrDraft.objects.count(), 0)

    def test_scarce_official_tasks_are_shared_between_drafts_of_one_number(self):
        # №17 has 10 official tasks; two drafts of 2 tasks x 4 variants need 8 each: five official for each, not 8 and 2.
        self.run_plan([{'title': 'КР А', 'numbers': {'17': 2}, 'variants': 4}, {'title': 'КР Б', 'numbers': {'17': 2}, 'variants': 4}])
        sources = dict(Task.objects.values_list('pk', 'exam_source'))
        counts = []
        for title in ('КР А', 'КР Б'):
            ids = [i for v in KrDraft.objects.get(title=title).data['variants'] for i in v]
            self.assertEqual(len(ids), 8)
            counts.append(sum(sources[i] == 'demo' for i in ids))
        self.assertEqual(counts, [5, 5])
