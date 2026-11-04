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

    def test_send_uses_current_metadata_without_separate_save(self):
        self.build()
        with patch.object(kr_client, 'create', return_value={'test': {'title': 'Новая КР'}, 'code': 'AB12CD'}) as create:
            self.client.post('/teacher/kr/draft/', {'action': 'send', 'title': 'Новая КР', 'group': 'Группа', 'duration': '120'})
        self.assertEqual(create.call_args.args[1:3], ('Новая КР', 'Группа'))
        self.assertEqual(create.call_args.args[6], 120)

    def test_drafts_are_private_to_their_teacher(self):
        self.build()
        row = KrDraft.objects.get()
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(f'/teacher/kr/drafts/{row.pk}/open/').status_code, 404)
        self.assertEqual(self.client.post(f'/teacher/kr/drafts/{row.pk}/delete/').status_code, 404)
        with patch.object(kr_client, 'tests_of', return_value=[]):
            self.assertNotContains(self.client.get('/teacher/kr/'), 'КР № 1')
        self.assertEqual(KrDraft.objects.count(), 1)

    def test_variants_can_be_built_by_hand(self):
        build = lambda data: self.client.post('/teacher/kr/', {'subject': 'inf', 'title': 'Вручную', 'variants': '3', 'duration': '45', **data})  # noqa: E731
        with patch.object(kr_client, 'tests_of', return_value=[]):
            self.assertContains(build({}), 'Собрать вручную')  # no counts: the error points to the manual way
            self.assertEqual(KrDraft.objects.count(), 0)
            self.assertRedirects(build({'manual': '1'}), '/teacher/kr/draft/', fetch_redirect_response=False)
        self.assertEqual(KrDraft.objects.get().data['variants'], [[], [], []])
        task = Task.objects.filter(topic__number=17).first()
        self.client.post('/teacher/kr/draft/', {'action': 'add', 'v': '1', 'task': str(task.pk)})  # the editor fills a variant from the catalogue
        self.assertEqual(KrDraft.objects.get().data['variants'], [[], [task.pk], []])
        self.client.post('/teacher/kr/draft/', {'action': 'send'})
        self.assertEqual(KrDraft.objects.count(), 1)  # an empty variant is not sent
        self.assertContains(self.client.get('/teacher/kr/draft/'), 'В каждом варианте должна быть хотя бы одна задача')

    def test_a_draft_a_variant_and_a_sent_kr_can_be_copied(self):
        self.build('Исходная')
        row = KrDraft.objects.get()
        # a draft: a new row with its own name, opened for editing; the original is untouched
        self.assertRedirects(self.client.post(f'/teacher/kr/drafts/{row.pk}/copy/'), '/teacher/kr/draft/', fetch_redirect_response=False)
        copy = KrDraft.objects.exclude(pk=row.pk).get()
        self.assertEqual((copy.title, copy.data['variants']), ('Исходная (копия)', row.data['variants']))
        self.assertEqual(self.client.session['kr_draft']['saved_id'], copy.pk)
        self.client.post('/teacher/kr/draft/', {'action': 'remove', 'v': '0', 'i': '0'})
        row.refresh_from_db()
        self.assertEqual(len(row.data['variants'][0]), 3)  # editing the copy leaves the original as it was
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(f'/teacher/kr/drafts/{row.pk}/copy/').status_code, 404)
        self.client.force_login(self.teacher)
        # a variant: the copy comes right after it
        self.client.post(f'/teacher/kr/drafts/{copy.pk}/open/')
        before = KrDraft.objects.get(pk=copy.pk).data['variants']
        self.client.post('/teacher/kr/draft/', {'action': 'copy_variant', 'v': '0'})
        after = KrDraft.objects.get(pk=copy.pk).data['variants']
        self.assertEqual((len(after), after[1], after[2]), (4, before[0], before[1]))
        # a КР that was already sent: its variants come back from the КР platform as a new draft; lost tasks are left out
        ids = [t.pk for t in Task.objects.filter(topic__number=5)[:3]]
        Task.objects.filter(pk=ids[2]).update(published=False)
        source = {'title': 'Старая КР', 'group': 'Пара 07', 'subject': 'inf', 'duration': 90, 'variants': [[ids[0], ids[2]], [ids[1], 987654]]}
        with patch.object(kr_client, 'variants_of', return_value=source):
            reply = self.client.post('/teacher/kr/copy/', {'test': '12'})
        self.assertRedirects(reply, '/teacher/kr/draft/', fetch_redirect_response=False)
        made = KrDraft.objects.get(title='Старая КР (копия)')
        self.assertEqual((made.data['variants'], made.data['group'], made.data['duration']), ([[ids[0]], [ids[1]]], 'Пара 07', 90))
        with patch.object(kr_client, 'variants_of', side_effect=kr_client.KrError('Такой КР нет.')):
            self.assertRedirects(self.client.post('/teacher/kr/copy/', {'test': '13'}), '/teacher/kr/', fetch_redirect_response=False)

    def test_the_picker_adds_without_leaving_and_marks_what_is_taken(self):
        self.build()
        ajax = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}
        task = Task.objects.filter(topic__number=8).order_by('-pk').first()  # the picker lists the newest first
        reply = self.client.post('/teacher/kr/draft/', {'action': 'add', 'v': '0', 'task': str(task.pk)}, **ajax)
        self.assertEqual(reply.status_code, 200)  # an answer for the page, not a redirect back to the draft
        self.assertEqual(reply.json(), {'ok': True, 'variant': 1, 'count': len(KrDraft.objects.get().data['variants'][0])})
        self.assertIn(task.pk, KrDraft.objects.get().data['variants'][0])
        again = self.client.post('/teacher/kr/draft/', {'action': 'add', 'v': '0', 'task': str(task.pk)}, **ajax).json()
        self.assertFalse(again['ok'])
        self.assertIn('уже есть', again['error'])  # the reason goes to the picker, not to a message of the next page
        self.assertContains(self.client.get('/teacher/kr/tasks/?number=8&draft=1'), 'уже в варианте: 1')
        self.assertNotContains(self.client.get('/teacher/kr/tasks/?number=8'), 'уже в варианте')  # only the draft's picker marks them

    def test_tasks_picked_in_the_catalogue_start_variant_one(self):
        seventeen, five = Task.objects.filter(topic__number=17).first(), Task.objects.filter(topic__number=5).first()
        with patch.object(kr_client, 'tests_of', return_value=[]):
            self.client.post('/teacher/kr/', {'subject': 'inf', 'title': 'Вручную', 'variants': '2', 'duration': '45', 'manual': '1',
                                              'picked': f'{seventeen.pk},{five.pk},{seventeen.pk},999999'})
        self.assertEqual(KrDraft.objects.get().data['variants'], [[five.pk, seventeen.pk], []])  # by number, once, only real tasks


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
