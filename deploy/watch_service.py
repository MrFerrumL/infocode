"""Bounded host-side recovery for Docker services; never restart the database.

Run once per minute from systemd. Three consecutive failures are required;
restarts have a 15-minute cooldown. A deployment marker suspends recovery.
No requests, credentials, form data or exception payloads are logged.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

THRESHOLD = 3
COOLDOWN = 15 * 60


def decide(state, observations, now):
    """Pure state transition; return services eligible for a single restart."""
    actions = []
    database_ready = observations.get('db') == 'healthy'
    for service in ('web', 'caddy'):
        unhealthy = database_ready and observations.get(service) == 'unhealthy'
        entry = state.setdefault(service, {'failures': 0, 'last_restart': None})
        entry['failures'] = entry['failures'] + 1 if unhealthy else 0
        last = entry.get('last_restart')
        if unhealthy and entry['failures'] >= THRESHOLD and (last is None or now - last >= COOLDOWN):
            actions.append(service)
            entry.update(failures=0, last_restart=now)
    return actions


def emit(event, **fields):
    print(json.dumps({'event': event, **fields}, ensure_ascii=True), flush=True)


def command(args, timeout=20):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True)


def status(compose, service):
    ids = command(compose + ['ps', '--all', '--quiet', service]).stdout.split()
    if len(ids) != 1:
        return 'missing'
    item = json.loads(command(['docker', 'inspect', ids[0]]).stdout)[0]['State']
    if item['Status'] != 'running':
        return item['Status']
    return item.get('Health', {}).get('Status', 'running')


def domain_from_env(path):
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.startswith('APP_DOMAIN='):
            value = line.partition('=')[2].strip().strip('"\'')
            if re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', value):
                return value
    raise ValueError('Invalid APP_DOMAIN')


def proxy_status(domain):
    # Loopback bypasses external DNS and network problems, but verifies the real
    # domain's TLS certificate. A bad certificate needs repair, not a restart.
    result = subprocess.run([
        'curl', '--silent', '--show-error', '--noproxy', '*',
        '--resolve', f'{domain}:443:127.0.0.1', '--connect-timeout', '3',
        '--max-time', '8', '--output', os.devnull, '--write-out', '%{http_code}',
        f'https://{domain}/health/',
    ], capture_output=True, text=True, timeout=12)
    if result.returncode == 0 and result.stdout == '200':
        return 'healthy'
    if result.returncode in (7, 28, 52, 56) or (result.returncode == 0 and result.stdout in ('502', '503', '504')):
        return 'unhealthy'
    return 'needs_attention'


def save_state(path, state):
    fd, temporary = tempfile.mkstemp(prefix='.state-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(state, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def deployment_active(root):
    if not (root / '.deploy-in-progress').exists():
        return False
    import fcntl
    try:
        with (root / '.deploy.lock').open('r') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
    except FileNotFoundError:
        pass
    # After a killed installer/reboot the marker may survive, but the kernel
    # releases its lock. Do not leave recovery suspended indefinitely.
    emit('stale_deployment_marker')
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/infocode'))
    parser.add_argument('--state-dir', type=Path, default=Path('/var/lib/infocode-watchdog'))
    args = parser.parse_args(argv)
    import fcntl  # host-side Linux service, not imported by Windows unit tests
    args.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (args.state_dir / 'watch.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        state_file = args.state_dir / 'state.json'
        if deployment_active(args.root):
            # Do not carry a stale failure count across planned maintenance.
            save_state(state_file, {})
            emit('deployment_in_progress')
            return 0
        try:
            try:
                state = json.loads(state_file.read_text(encoding='utf-8'))
            except FileNotFoundError:
                state = {}
            compose = ['docker', 'compose', '--project-directory', str(args.root),
                       '--env-file', str(args.root / '.env.production'),
                       '-f', str(args.root / 'compose.production.yaml')]
            observed = {name: status(compose, name) for name in ('db', 'web', 'caddy')}
            if observed['web'] == 'healthy' and observed['caddy'] in ('running', 'healthy'):
                observed['caddy'] = proxy_status(domain_from_env(args.root / '.env.production'))
            else:
                # The proxy cannot be judged while its upstream is unavailable.
                observed['caddy'] = 'waiting_for_upstream'
            actions = decide(state, observed, time.time())
            # Persist the cooldown before attempting any restart, even if it fails.
            save_state(state_file, state)
            emit('health', services=observed, recovery=actions)
            for service in actions:
                # A deployment may have started since the first check.
                if deployment_active(args.root):
                    break
                command(compose + ['restart', service], timeout=50)
                emit('restarted', service=service)
            return 0
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            emit('monitor_error', kind=type(exc).__name__)
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
