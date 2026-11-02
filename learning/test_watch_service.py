import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from deploy.watch_service import COOLDOWN, decide, deployment_active, domain_from_env, proxy_status, save_state


class RecoveryTests(SimpleTestCase):
    def test_transient_failures_do_not_restart_and_success_resets_count(self):
        state = {}
        down = {'db': 'healthy', 'web': 'unhealthy'}
        self.assertEqual(decide(state, down, 100), [])
        self.assertEqual(decide(state, down, 160), [])
        self.assertEqual(decide(state, {'db': 'healthy', 'web': 'healthy'}, 220), [])
        self.assertEqual(decide(state, down, 280), [])
        self.assertEqual(state['web']['failures'], 1)

    def test_persistent_failure_is_bounded_by_cooldown(self):
        state = {}
        down = {'db': 'healthy', 'web': 'unhealthy'}
        for now in (100, 160):
            self.assertEqual(decide(state, down, now), [])
        self.assertEqual(decide(state, down, 220), ['web'])
        for now in range(280, 220 + COOLDOWN, 60):
            self.assertEqual(decide(state, down, now), [])
        self.assertEqual(decide(state, down, 220 + COOLDOWN), ['web'])

    def test_database_failure_and_planned_stop_never_trigger_restarts(self):
        for observed in (
            {'db': 'unhealthy', 'web': 'unhealthy', 'caddy': 'unhealthy'},
            {'db': 'healthy', 'web': 'exited', 'caddy': 'waiting_for_upstream'},
            {'db': 'healthy', 'web': 'starting', 'caddy': 'waiting_for_upstream'},
        ):
            state = {}
            for now in range(0, 1200, 60):
                self.assertEqual(decide(state, observed, now), [])

    def test_only_the_failing_service_is_restarted(self):
        state = {}
        observed = {'db': 'healthy', 'web': 'healthy', 'caddy': 'unhealthy'}
        decide(state, observed, 100)
        decide(state, observed, 160)
        self.assertEqual(decide(state, observed, 220), ['caddy'])

    def test_certificate_errors_are_reported_without_a_restart(self):
        with patch('deploy.watch_service.subprocess.run') as run:
            run.return_value.returncode = 60
            run.return_value.stdout = '000'
            self.assertEqual(proxy_status('infocode1.ru'), 'needs_attention')
            args = run.call_args.args[0]
            self.assertNotIn('--insecure', args)
            self.assertIn('infocode1.ru:443:127.0.0.1', args)

    def test_environment_domain_and_state_file(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            env = folder / '.env.production'
            env.write_text('APP_DOMAIN=infocode1.ru\nSECRET_KEY=not-read\n')
            self.assertEqual(domain_from_env(env), 'infocode1.ru')
            env.write_text('APP_DOMAIN=https://other.example/path\n')
            with self.assertRaises(ValueError):
                domain_from_env(env)
            target = folder / 'state.json'
            save_state(target, {'web': {'failures': 1}})
            self.assertEqual(json.loads(target.read_text()), {'web': {'failures': 1}})
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ['.env.production', 'state.json'])

    def test_stale_deployment_marker_cannot_disable_recovery_forever(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.deploy-in-progress').write_text('interrupted release')
            (root / '.deploy.lock').touch()
            fake_fcntl = Mock(LOCK_EX=2, LOCK_NB=4)
            with patch.dict('sys.modules', {'fcntl': fake_fcntl}), patch('deploy.watch_service.emit'):
                self.assertFalse(deployment_active(root))
                fake_fcntl.flock.side_effect = BlockingIOError
                self.assertTrue(deployment_active(root))
