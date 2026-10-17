from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.template import Context, Template
from django.urls import reverse
from django.utils import timezone

from learning.models import Practice, Task, TaskBlock, Topic
from learning.services import create_variant


class CatalogConditionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student = get_user_model().objects.create_user('conditions-reader')
        cls.topic = Topic.objects.create(number=1, title='Графы')
        cls.other = Topic.objects.create(number=2, title='Логика')
        cls.tasks = []
        for number in range(5):
            task = Task.objects.create(topic=cls.topic, title=f'Открытое условие {number}',
                answer='PRIVATE_CONDITION_ANSWER', solution='PRIVATE_CONDITION_SOLUTION',
                source_system='kompege', external_id=31000+number,
                source_url=f'https://kompege.ru/task?id={31000+number}')
            TaskBlock.objects.create(task=task, kind='image', image=f'imports/kompege/{31000+number}/old/condition.jpg')
            TaskBlock.objects.create(task=task, kind='text', purpose='solution', text='PRIVATE_SOLUTION_BLOCK')
            cls.tasks.append(task)

    def setUp(self):
        self.client.force_login(self.student)

    def test_selected_number_displays_all_five_conditions_without_opening_task_pages(self):
        Task.objects.create(topic=self.other, title='Другой номер', answer='1')
        response = self.client.get('/catalog/?number=1')
        self.assertContains(response, 'catalog-condition-body', count=5)
        for task in self.tasks:
            block = task.content_blocks.get(kind='image')
            self.assertContains(response, f'src="{reverse("download", args=["block", block.pk])}"')
            self.assertContains(response, task.source_url)
        self.assertNotContains(response, 'Другой номер')
        self.assertNotContains(response, '?preview=1')
        self.assertFalse(Practice.objects.exists())

    def test_catalog_never_reveals_answers_or_solution_blocks(self):
        response = self.client.get('/catalog/?number=1')
        for secret in ('PRIVATE_CONDITION_ANSWER', 'PRIVATE_CONDITION_SOLUTION', 'PRIVATE_SOLUTION_BLOCK'):
            self.assertNotContains(response, secret)

    def test_full_authored_text_and_attachments_are_visible_and_html_is_escaped(self):
        task = self.tasks[0]
        task.statement = 'Условие ' * 50 + '<script>evil()</script> FINISH_CONDITION'
        task.attachment = 'tasks/data.txt'
        task.save()
        file = TaskBlock.objects.create(task=task, kind='file', file='task-blocks/table.ods', text='table.ods')
        response = self.client.get('/catalog/?number=1')
        self.assertContains(response, 'FINISH_CONDITION')
        self.assertContains(response, '&lt;script&gt;evil()&lt;/script&gt;')
        self.assertNotContains(response, '<script>evil()</script>')
        self.assertContains(response, reverse('download', args=['task', task.pk]))
        self.assertContains(response, reverse('download', args=['block', file.pk]))

    def test_archived_or_draft_conditions_and_archived_blocks_do_not_appear(self):
        self.tasks[0].published = False
        self.tasks[0].save()
        self.tasks[1].archived_at = timezone.now()
        self.tasks[1].save()
        TaskBlock.objects.create(task=self.tasks[2], kind='text', text='ARCHIVED_BLOCK_SECRET', archived_at=timezone.now())
        response = self.client.get('/catalog/?number=1')
        self.assertContains(response, 'catalog-condition-body', count=3)
        self.assertNotContains(response, self.tasks[0].title)
        self.assertNotContains(response, self.tasks[1].title)
        self.assertNotContains(response, 'ARCHIVED_BLOCK_SECRET')

    def test_normal_images_keep_bounded_previews_and_imported_detail_keeps_original(self):
        task = self.tasks[0]
        normal = TaskBlock.objects.create(task=task, kind='image', image='task-images/own-photo.jpg')
        response = self.client.get(reverse('task', args=[task.pk]))
        self.assertContains(response, f'src="{reverse("download", args=["block", normal.pk])}?preview=1"')
        self.assertContains(response, 'source-condition')
        original = task.content_blocks.get(image__startswith='imports/')
        self.assertContains(response, f'src="{reverse("download", args=["block", original.pk])}"')

    def test_query_count_does_not_grow_for_each_additional_condition(self):
        with CaptureQueriesContext(connection) as few:
            self.client.get('/catalog/?number=1')
        for index in range(5, 12):
            task = Task.objects.create(topic=self.topic, title=f'Ещё условие {index}', answer='1')
            TaskBlock.objects.create(task=task, kind='text', text='Полное условие')
        with CaptureQueriesContext(connection) as many:
            response = self.client.get('/catalog/?number=1')
        self.assertContains(response, 'catalog-condition-body', count=12)
        self.assertEqual(len(few), len(many))

    def test_variant_snapshot_preserves_original_image_and_file_access_route(self):
        variant = create_variant(creator=self.student, title='Проверка снимка', topics=[], count=1, task_ids=str(self.tasks[0].pk))
        item = variant.items.get()
        html = Template('{% load content_blocks %}{% task_blocks item %}').render(Context({'item': item}))
        self.assertIn('source-condition', html)
        self.assertIn(reverse('download', args=['snapshot', item.pk])+'?block=0', html)
        self.assertNotIn('preview=1', html)
        self.assertNotIn('PRIVATE_SOLUTION_BLOCK', html)
