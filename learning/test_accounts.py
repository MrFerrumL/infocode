import json
import io
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import DatabaseError
from django.core.management import call_command, CommandError
from django.test import TestCase, Client, SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from .models import AuditEvent, GroupInvite, InviteUse, StudentAccess, StudentAssignment, StudyGroup
from .security import form_token


class AccountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user('access-teacher', is_staff=True)
        cls.other = User.objects.create_user('other-teacher', is_staff=True)
        cls.student = User.objects.create_user('existing-student', password='Existing!Pass734')
        StudentAssignment.objects.create(teacher=cls.teacher, student=cls.student)
        cls.group = StudyGroup.objects.create(title='Моя группа', teacher=cls.teacher)
        cls.foreign_group = StudyGroup.objects.create(title='Чужая группа', teacher=cls.other)

    def setUp(self):
        self.client.force_login(self.teacher)

    def create(self, **changes):
        data = {'first_name': 'Иван', 'last_name': 'Петров', 'group': self.group.pk,
                'submit_token': form_token(self.teacher, 'student-account-create')}
        data.update(changes)
        return self.client.post(reverse('student_account_create'), data)

    def test_teacher_issues_hashed_temporary_password_and_attaches_group(self):
        page = self.create(is_staff='on', is_superuser='on', username='chosen')
        self.assertEqual(page.status_code, 200)
        student = User.objects.get(username='petrov.ivan')
        password = page.context['temporary_password']
        self.assertRegex(password, r'^[a-z]{4}[2-9]{4}$')
        self.assertTrue(student.check_password(password))
        self.assertFalse(student.is_staff or student.is_superuser)
        self.assertTrue(self.group.students.filter(pk=student.pk).exists())
        self.assertTrue(StudentAssignment.objects.filter(teacher=self.teacher, student=student).exists())
        self.assertFalse(student.student_access.must_change_password)
        self.assertIn('no-store', page['Cache-Control'])
        self.assertEqual(page['Referrer-Policy'], 'no-referrer')
        self.assertNotIn(password, json.dumps(list(AuditEvent.objects.values('title', 'changes'))))
        self.assertNotContains(self.client.get(reverse('teacher_students')), password)

    def test_no_foreign_group_or_duplicate_login_or_unscoped_token(self):
        page = self.create(group=self.foreign_group.pk)
        self.assertTrue(page.context['form'].errors)
        self.assertFalse(User.objects.filter(username='petrov.ivan').exists())
        self.create()
        page = self.create()
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context['student'].username, 'petrov.ivan2')
        self.assertEqual(self.create(first_name='Ёжик', last_name='Щукин-Юрьев').context['student'].username, 'shchukinyurev.ezhik')
        self.assertEqual(self.create(submit_token='').status_code, 403)
        self.assertEqual(self.create(submit_token=form_token(self.other, 'student-account-create')).status_code, 403)

    def test_issued_password_works_at_once_and_any_new_password_is_accepted(self):
        page = self.create()
        password = page.context['temporary_password']
        self.client.logout()
        self.assertTrue(self.client.login(username='petrov.ivan', password=password))
        self.assertEqual(self.client.get('/catalog/').status_code, 200)
        page = self.client.post('/password/', {'old_password': password, 'new_password1': password, 'new_password2': password})
        self.assertContains(page, 'Новый пароль должен отличаться')
        self.assertRedirects(self.client.post('/password/', {'old_password': password, 'new_password1': '1234', 'new_password2': '1234'}), '/')
        self.assertTrue(User.objects.get(username='petrov.ivan').check_password('1234'))

    def test_registration_accepts_a_simple_password(self):
        self.client.logout()
        page = self.client.post('/register/', {'first_name': 'Иван Куликов', 'username': 'ivan', 'password1': '1111', 'password2': '1111'})
        self.assertEqual(page.status_code, 302)
        self.assertTrue(User.objects.get(username='ivan').check_password('1111'))

    def test_reset_scopes_authority_and_invalidates_old_session_once(self):
        pupil = Client()
        pupil.force_login(self.student)
        url = reverse('student_password_reset', args=[self.student.pk])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(reverse('student_password_reset', args=[self.teacher.pk])).status_code, 404)
        token = form_token(self.teacher, f'student-password:{self.student.pk}')
        page = self.client.post(url, {'submit_token': token})
        password = page.context['temporary_password']
        self.student.refresh_from_db()
        self.assertTrue(self.student.check_password(password))
        self.assertEqual(pupil.get('/catalog/').status_code, 302)
        self.client.post(url, {'submit_token': token})
        self.student.refresh_from_db()
        self.assertTrue(self.student.check_password(password))

    def test_students_cannot_create_or_reset_accounts_and_post_requires_csrf(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse('student_account_create')).status_code, 302)
        self.assertEqual(self.client.post(reverse('student_password_reset', args=[self.student.pk])).status_code, 302)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.teacher)
        self.assertEqual(client.post(reverse('student_account_create'), {}).status_code, 403)

    def test_public_invite_registration_preserves_destination_without_consuming_get(self):
        invite = GroupInvite.objects.create(group=self.group, teacher=self.teacher, expires_at=timezone.now()+timedelta(days=1))
        url = reverse('join_group', args=[invite.token])
        self.client.logout()
        self.assertContains(self.client.get(url), 'Зарегистрироваться и продолжить')
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertFalse(InviteUse.objects.exists())
        page = self.client.post('/register/?next='+url, {'first_name': 'Новый ученик', 'username': 'self-registered',
            'password1': 'ZebraGraph!209781', 'password2': 'ZebraGraph!209781'})
        self.assertRedirects(page, url)
        self.assertFalse(StudentAccess.objects.filter(user__username='self-registered').exists())
        self.assertRedirects(self.client.post(url), '/assignments/')
        self.assertTrue(self.group.students.filter(username='self-registered').exists())
        self.assertTrue(StudentAssignment.objects.filter(teacher=self.teacher, student__username='self-registered').exists())

    def test_health_has_no_details_when_database_unavailable(self):
        self.client.logout()
        self.assertEqual(self.client.get('/health/').json(), {'status': 'ok'})
        with patch('learning.health.connection.cursor', side_effect=DatabaseError('private details')):
            page = self.client.get('/health/')
        self.assertEqual(page.status_code, 503)
        self.assertEqual(page.json(), {'status': 'unavailable'})


class BackupPublicationTests(TestCase):
    def test_large_media_is_streamed_and_existing_backup_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            media = folder / 'uploads'
            media.mkdir()
            content = b'course-media' * 200_000
            (media/'lesson.bin').write_bytes(content)
            target = folder/'backup.zip'
            with self.settings(MEDIA_ROOT=media), patch.object(Path, 'read_bytes', side_effect=AssertionError('Read media in chunks')):
                call_command('backup_platform', str(target), stdout=io.StringIO())
                call_command('backup_platform', str(target), verify=True, stdout=io.StringIO())
                with self.assertRaises(CommandError):
                    call_command('backup_platform', str(target), stdout=io.StringIO())
            with zipfile.ZipFile(target) as archive:
                self.assertEqual(archive.read('media/lesson.bin'), content)
            self.assertFalse(list(folder.glob('.infocode-backup-*')))

    def test_failed_verification_does_not_publish_a_partial_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target = folder/'backup.zip'
            with self.settings(MEDIA_ROOT=folder/'uploads'), patch('learning.management.commands.backup_platform.Command.verify_archive', side_effect=CommandError('Failed verification')):
                with self.assertRaises(CommandError):
                    call_command('backup_platform', str(target), stdout=io.StringIO())
            self.assertFalse(target.exists())
            self.assertFalse(list(folder.iterdir()))


class DeploymentConfigTests(SimpleTestCase):
    def test_backup_retention_runs_only_after_verification_and_once_per_day(self):
        from learning.management.commands.run_maintenance import Command
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for day in ('01', '02', '03'):
                (folder / f'infocode-2020-01-{day}.zip').write_bytes(b'old archive')
            unrelated = folder / 'manual-copy.zip'
            unrelated.write_bytes(b'keep')
            def execute(name, *args, **kwargs):
                if name == 'backup_platform':
                    Path(args[0]).write_bytes(b'verified archive')
            command = Command()
            with patch('learning.management.commands.run_maintenance.call_command', side_effect=execute) as call:
                day = command.cycle(folder, 3, None)
                self.assertEqual(len(list(folder.glob('infocode-*.zip'))), 3)
                self.assertTrue(unrelated.exists())
                self.assertFalse((folder/'infocode-2020-01-01.zip').exists())
                call.reset_mock()
                command.cycle(folder, 3, day)
                call.assert_called_once_with('expire_attempts', verbosity=0)

    def test_failed_backup_never_deletes_previous_archives(self):
        from learning.management.commands.run_maintenance import Command
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for day in ('01', '02', '03'):
                (folder / f'infocode-2020-01-{day}.zip').write_bytes(b'old archive')
            def execute(name, *args, **kwargs):
                if name == 'backup_platform':
                    raise OSError('backup failed')
            with patch('learning.management.commands.run_maintenance.call_command', side_effect=execute):
                with self.assertRaises(OSError):
                    Command().cycle(folder, 1, None)
            self.assertEqual(len(list(folder.iterdir())), 3)

    def test_generated_environment_is_unique_validated_and_never_overwritten(self):
        from deploy.configure import generate
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / '.env.production'
            generate('ege.example.ru', 'teacher@example.ru', target)
            first = target.read_text(encoding='utf-8')
            with self.assertRaises(FileExistsError):
                generate('ege.example.ru', 'teacher@example.ru', target)
            self.assertEqual(first, target.read_text(encoding='utf-8'))
            for domain in ['https://ege.example.ru', '127.0.0.1', 'evil.example\nDEBUG=1']:
                with self.assertRaises(ValueError):
                    generate(domain, 'teacher@example.ru', Path(folder)/'bad')

    def test_proxy_headers_are_explicitly_trusted_only_when_configured(self):
        from serve import server_options
        from waitress.adjustments import Adjustments
        with patch.dict('os.environ', {'TRUSTED_PROXY': ''}):
            self.assertNotIn('trusted_proxy', server_options())
        with patch.dict('os.environ', {'TRUSTED_PROXY': '*'}):
            config = Adjustments(**server_options())
            self.assertEqual(config.trusted_proxy_headers, {'x-forwarded-for', 'x-forwarded-proto'})
