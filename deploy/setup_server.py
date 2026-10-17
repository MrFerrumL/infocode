"""Prepare the VPS from Windows without entering an interactive Linux shell."""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='195.209.211.242')
    parser.add_argument('--user', default='ubuntu')
    parser.add_argument('--key', type=Path, default=Path.home() / 'Downloads' / 'privatekey-1152809.pem')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:-]{0,252}', args.host):
        parser.error('Use a server IP or hostname without a protocol or spaces.')
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', args.user):
        parser.error('Invalid SSH username.')
    ssh = shutil.which('ssh')
    if not ssh:
        print('OpenSSH client was not found. Run this command in the Windows terminal where ssh works.', file=sys.stderr)
        return 2
    key = args.key.expanduser().resolve()
    if not key.is_file():
        print(f'SSH key not found: {key}', file=sys.stderr)
        return 2
    script = Path(__file__).with_name('bootstrap_ubuntu.sh')
    if not script.is_file():
        print(f'Installer file not found: {script}', file=sys.stderr)
        return 2

    # Send Linux commands as bytes to the remote shell, never to local PowerShell.
    # Normalise line endings even if Git checked out the script with CRLF on Windows.
    payload = script.read_bytes().replace(b'\r\n', b'\n')
    command = [
        ssh, '-T',
        '-o', 'IdentitiesOnly=yes',
        '-o', 'BatchMode=yes',
        '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ServerAliveInterval=20',
        '-o', 'ServerAliveCountMax=6',
        '-o', 'ConnectTimeout=15',
        '-i', str(key), f'{args.user}@{args.host}',
    ]
    print(f'Preparing server {args.host}. Please keep this terminal open.', flush=True)
    try:
        for remote_command in ('bash -n', 'sudo -n bash -s'):
            # Validate with the VPS Bash before executing any installation command.
            result = subprocess.run(command + [remote_command], input=payload, check=False)
            if result.returncode:
                print('\nServer setup did not finish. See the error above; the website has not been deployed.', file=sys.stderr)
                return result.returncode
    except OSError as exc:
        print(f'Could not start SSH: {exc}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('\nInterrupted. Check the server state before retrying.', file=sys.stderr)
        return 130
    print('\nServer preparation finished. Next: upload and configure the platform.', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
