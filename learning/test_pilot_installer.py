import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

from django.test import SimpleTestCase

from deploy.install_kompege_pilot import main, remote_script, validate_archives


class PilotInstallerTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.key = self.folder / 'placeholder.pem'
        self.key.write_text('test placeholder, not a private key', encoding='ascii')
        self.source = self.folder / 'source.zip'
        self.bundle = self.folder / 'pilot.zip'
        with zipfile.ZipFile(self.source, 'w') as archive:
            archive.writestr('infocode/learning/management/commands/import_kompege_pilot.py', 'fixture')
            archive.writestr('infocode/learning/migrations/0014_task_source_reference.py', 'fixture')
        rows = [{'number': number} for number in range(1, 28) for _ in range(5)]
        with zipfile.ZipFile(self.bundle, 'w') as archive:
            archive.writestr('manifest.json', json.dumps({'format': 'infocode-kompege-pilot-v1', 'tasks': rows}))

    def run_installer(self, codes):
        from contextlib import redirect_stdout, redirect_stderr
        output, errors = io.StringIO(), io.StringIO()
        response = io.BytesIO(b'{"status":"ok"}')
        response.status = 200
        with patch('deploy.install_kompege_pilot.shutil.which', side_effect=lambda name: name + '.exe'), \
                patch('deploy.install_kompege_pilot.subprocess.run', side_effect=[SimpleNamespace(returncode=code, stdout=out) for code, out in
                      ((c if isinstance(c, tuple) else (c, b'')) for c in codes)]) as run, \
                patch('deploy.install_kompege_pilot.time.sleep'), \
                patch('deploy.install_kompege_pilot.urlopen', return_value=response) as health, \
                redirect_stdout(output), redirect_stderr(errors):
            result = main(['--key', str(self.key), '--source', str(self.source), '--bundle', str(self.bundle)])
        return result, output.getvalue(), errors.getvalue(), run, health

    def test_backup_precedes_deployment_and_only_pilot_is_imported(self):
        payload = remote_script('ubuntu').decode('utf-8')
        self.assertLess(payload.index('backup_platform'), payload.index('unzip -oq'))
        self.assertLess(payload.index('run --rm migrate'), payload.index('import_kompege_pilot'))
        self.assertIn('--publish --sync-empty-topic-titles', payload)
        self.assertNotIn('seed_demo', payload)
        self.assertNotIn('down -v', payload)
        self.assertIn('check_content --strict', payload)
        # Old installer backups are pruned only after the new one exists.
        self.assertLess(payload.index('backup_platform'), payload.index('tail -n +4 | xargs -r rm -f'))

    def test_fipi_bundle_is_imported_after_backup_then_removed(self):
        payload = remote_script('ubuntu', fipi=True).decode('utf-8')
        self.assertLess(payload.index('backup_platform'), payload.index('import_fipi /tmp/fipi-bank.zip'))
        self.assertLess(payload.index('import_fipi'), payload.index('rm -f /home/ubuntu/fipi-bank.zip'))

    def test_physics_content_is_seeded_only_on_request(self):
        self.assertNotIn('seed_physics', remote_script('ubuntu').decode('utf-8'))
        payload = remote_script('ubuntu', physics=True).decode('utf-8')
        self.assertLess(payload.index('run --rm migrate'), payload.index('manage.py seed_physics'))

    def test_polyakov_bundles_are_imported_after_backup_then_removed(self):
        payload = remote_script('ubuntu', polyakov=[([1, 2], 'polyakov-1.zip'), ([19, 20, 21], 'polyakov-2.zip')]).decode('utf-8')
        first = payload.index('import_kompege_numbers /tmp/polyakov-1.zip --numbers 1 2 --publish')
        self.assertLess(payload.index('backup_platform'), first)
        self.assertLess(first, payload.index('rm -f /home/ubuntu/polyakov-1.zip'))
        self.assertIn('/tmp/polyakov-2.zip --numbers 19 20 21 --publish', payload)
        self.assertLess(payload.index('polyakov-2.zip --numbers'), payload.index('sync_similar_tasks'))

    def test_verified_upload_and_success_after_remote_and_public_checks(self):
        # syntax check, two uploads, script upload, detached start, a dropped poll, the finished log, cleanup
        result, output, errors, run, health = self.run_installer([0, 0, 0, 0, 0, 255, (0, b'[6/6] ok\nINFOCODE_EXIT=0\n'), 0])
        self.assertEqual(result, 0)
        self.assertEqual(errors, '')
        self.assertEqual(run.call_count, 8)
        self.assertEqual(run.call_args_list[0].args[0][-1], 'bash -n')
        self.assertEqual(run.call_args_list[0].kwargs['input'], run.call_args_list[3].kwargs['input'])
        self.assertIn('cat > /home/ubuntu/infocode-install.sh', run.call_args_list[3].args[0][-1])
        start = run.call_args_list[4].args[0][-1]
        self.assertIn('nohup sudo -n setsid bash', start)
        self.assertIn('INFOCODE_EXIT=$?', start)
        self.assertIn('StrictHostKeyChecking=yes', run.call_args.args[0])
        self.assertIsInstance(run.call_args_list[3].kwargs['input'], bytes)
        self.assertIn('[6/6] ok', output)
        self.assertIn('Pilot installed and health checked', output)
        health.assert_called_once_with('https://infocode1.ru/health/', timeout=20)
        self.assertEqual(self.key.read_text(encoding='ascii'), 'test placeholder, not a private key')

    def test_upload_failure_does_not_execute_remote_commands(self):
        result, output, errors, run, health = self.run_installer([0, 255])
        self.assertEqual(result, 255)
        self.assertEqual(run.call_count, 2)
        health.assert_not_called()
        self.assertNotIn('Pilot installed', output)

    def test_remote_failure_is_not_reported_as_success(self):
        result, output, errors, run, health = self.run_installer([0, 0, 0, 0, 0, (0, b'stopped at line 9\nINFOCODE_EXIT=1\n'), 0])
        self.assertEqual(result, 1)
        self.assertIn('Installation did not finish', errors)
        health.assert_not_called()
        self.assertNotIn('Pilot installed', output)

    def test_failed_remote_syntax_check_prevents_uploads(self):
        result, output, errors, run, health = self.run_installer([255])
        self.assertEqual(result, 255)
        run.assert_called_once()
        health.assert_not_called()
        self.assertIn('nothing was installed', errors)

    def test_configuration_secrets_in_source_are_rejected_before_upload(self):
        with zipfile.ZipFile(self.source, 'a') as archive:
            archive.writestr('infocode/.env.production', 'test placeholder')
        with self.assertRaisesRegex(ValueError, 'Production secrets'):
            validate_archives(self.source, self.bundle)

    def test_larger_bank_is_rejected(self):
        with zipfile.ZipFile(self.bundle, 'w') as archive:
            archive.writestr('manifest.json', json.dumps({'format': 'infocode-kompege-pilot-v1', 'tasks': [{'number': 1}] * 136}))
        with self.assertRaisesRegex(ValueError, 'Only the 135-card pilot'):
            validate_archives(self.source, self.bundle)

    def test_explicit_categories_are_scoped_and_import_only_after_backup(self):
        payload = remote_script('ubuntu', categories=True, numbers=[3, 2]).decode('utf-8')
        self.assertIn('import_kompege_numbers /tmp/kompege-categories.zip --numbers 2 3 --publish', payload)
        self.assertLess(payload.index('backup_platform'), payload.index('import_kompege_numbers'))
        with self.assertRaises(ValueError):
            remote_script('ubuntu', categories=True, numbers=[0])
