from pathlib import Path
import tempfile

from django.test import SimpleTestCase

from deploy.configure import generate


class DeploymentConfigTests(SimpleTestCase):
    def test_ascii_email_is_trimmed_and_secrets_are_generated(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / '.env.production'
            generate(' INFOCODE1.RU ', ' teacher@example.ru ', target)
            values = dict(line.split('=', 1) for line in target.read_text(encoding='utf-8').splitlines())
        self.assertEqual(values['APP_DOMAIN'], 'infocode1.ru')
        self.assertEqual(values['ACME_EMAIL'], 'teacher@example.ru')
        self.assertGreaterEqual(len(values['SECRET_KEY']), 64)
        self.assertGreaterEqual(len(values['DB_PASSWORD']), 40)

    def test_cyrillic_lookalikes_are_rejected_before_creating_secrets(self):
        for email in ('te\u0430cher@example.ru', 'teacher@ex\u0430mple.ru', '\u044c' + 'teacher@example.ru'):
            with self.subTest(email=email), tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / '.env.production'
                with self.assertRaisesRegex(ValueError, 'ASCII'):
                    generate('infocode1.ru', email, target)
                self.assertFalse(target.exists())

    def test_malformed_email_does_not_create_configuration(self):
        for email in ('', 'teacher', 'teacher@example', 'teacher name@example.ru', 'teacher#name@example.ru', 'teacher@example.ru\nSECRET_KEY=bad'):
            with self.subTest(email=email), tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / '.env.production'
                with self.assertRaises(ValueError):
                    generate('infocode1.ru', email, target)
                self.assertFalse(target.exists())

    def test_existing_production_secrets_are_never_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / '.env.production'
            original = b'APP_DOMAIN=infocode1.ru\nSECRET_KEY=keep-this\nDB_PASSWORD=keep-that\n'
            target.write_bytes(original)
            with self.assertRaises(FileExistsError):
                generate('infocode1.ru', 'teacher@example.ru', target)
            self.assertEqual(target.read_bytes(), original)
