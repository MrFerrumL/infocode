import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from deploy.setup_server import main


class ServerSetupTests(SimpleTestCase):
    def run_setup(self, key, returncode=0):
        output = io.StringIO()
        errors = io.StringIO()
        with patch('deploy.setup_server.shutil.which', return_value='ssh.exe'), \
                patch('deploy.setup_server.subprocess.run', return_value=SimpleNamespace(returncode=returncode)) as run, \
                redirect_stdout(output), redirect_stderr(errors):
            result = main(['--key', str(key)])
        return result, output.getvalue(), errors.getvalue(), run

    def test_missing_key_does_not_attempt_ssh(self):
        with tempfile.TemporaryDirectory() as folder:
            result, output, errors, run = self.run_setup(Path(folder) / 'missing.pem')
        self.assertEqual(result, 2)
        run.assert_not_called()
        self.assertIn('SSH key not found', errors)
        self.assertNotIn('preparation finished', output)

    def test_installer_is_sent_to_verified_remote_shell_as_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder) / 'test.pem'
            key.write_text('test placeholder, not a private key', encoding='ascii')
            result, output, errors, run = self.run_setup(key)
            command = run.call_args.args[0]
            self.assertEqual(command[0], 'ssh.exe')
            self.assertIn('StrictHostKeyChecking=yes', command)
            self.assertIn('BatchMode=yes', command)
            self.assertEqual(command[-2:], ['ubuntu@195.209.211.242', 'sudo -n bash -s'])
            payload = run.call_args.kwargs['input']
            self.assertIsInstance(payload, bytes)
            self.assertNotIn(b'\r', payload)
            self.assertIn(b'INFOCODE_SERVER_READY', payload)
            self.assertIn(b'ufw allow 22/tcp', payload)
            self.assertLess(payload.index(b'ufw allow 22/tcp'), payload.index(b'ufw --force enable'))
            self.assertEqual(key.read_text(encoding='ascii'), 'test placeholder, not a private key')
        self.assertEqual(result, 0)
        self.assertEqual(errors, '')
        self.assertIn('Server preparation finished', output)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0][-1], 'bash -n')
        self.assertEqual(run.call_args_list[0].kwargs['input'], run.call_args_list[1].kwargs['input'])

    def test_remote_failure_is_returned_without_claiming_success(self):
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder) / 'test.pem'
            key.write_text('test placeholder, not a private key', encoding='ascii')
            result, output, errors, run = self.run_setup(key, returncode=255)
        self.assertEqual(result, 255)
        run.assert_called_once()
        self.assertIn('Server setup did not finish', errors)
        self.assertNotIn('Server preparation finished', output)

    def test_failed_installation_after_syntax_check_does_not_claim_success(self):
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder) / 'test.pem'
            key.write_text('test placeholder, not a private key', encoding='ascii')
            output = io.StringIO()
            errors = io.StringIO()
            with patch('deploy.setup_server.shutil.which', return_value='ssh.exe'), \
                    patch('deploy.setup_server.subprocess.run', side_effect=[SimpleNamespace(returncode=0), SimpleNamespace(returncode=100)]) as run, \
                    redirect_stdout(output), redirect_stderr(errors):
                result = main(['--key', str(key)])
        self.assertEqual(result, 100)
        self.assertEqual(run.call_count, 2)
        self.assertIn('Server setup did not finish', errors.getvalue())
        self.assertNotIn('Server preparation finished', output.getvalue())
