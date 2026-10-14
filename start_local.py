"""One-command local setup for Python 3.10+ (recommended: 3.12)."""
import hashlib
import os
import secrets
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    os.chdir(ROOT)
    environment = ROOT / '.venv'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if '--ready' not in sys.argv:
        if not python.exists():
            print('Creating a private Python environment...', flush=True)
            venv.EnvBuilder(with_pip=True).create(environment)
        digest = hashlib.sha256((ROOT / 'requirements.txt').read_bytes()).hexdigest()
        stamp = environment / '.requirements-sha256'
        if not stamp.exists() or stamp.read_text() != digest:
            subprocess.run([str(python), '-m', 'pip', 'install', '-r', 'requirements.txt'], check=True)
            stamp.write_text(digest)
        return subprocess.call([str(python), str(Path(__file__).resolve()), '--ready'])
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    call_command('migrate', interactive=False)
    owner = get_user_model().objects.filter(is_superuser=True, is_active=True).first()
    if owner is None:
        password = secrets.token_urlsafe(18)
        owner = get_user_model().objects.create_superuser('admin', '', password, first_name='Преподаватель')
        credentials = f'Local URL: http://127.0.0.1:8000/\nUsername: admin\nPassword: {password}\n'
        (ROOT / '.local-credentials.txt').write_text(credentials, encoding='utf-8')
        print(credentials, flush=True)
    call_command('seed_demo', owner=owner.username)
    call_command('collectstatic', interactive=False, verbosity=0)
    print('\nOpen http://127.0.0.1:8000/\nPress Ctrl+C to stop.\n', flush=True)
    from config.wsgi import application
    from waitress import serve
    from threading import Event, Thread
    from django.db import close_old_connections
    import logging
    stop_reminders = Event()
    def reminder_loop():
        from learning.schedule_services import send_reminders
        while not stop_reminders.is_set():
            try:
                close_old_connections()
                send_reminders()
            except Exception:
                logging.getLogger('platform').exception('Local schedule reminders failed')
            finally:
                close_old_connections()
            stop_reminders.wait(60)
    reminders = Thread(target=reminder_loop, name='schedule-reminders', daemon=True)
    reminders.start()
    try:
        serve(application, host='127.0.0.1', port=8000, threads=8, max_request_body_size=525_000_000)
    finally:
        stop_reminders.set()
        reminders.join(timeout=2)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
