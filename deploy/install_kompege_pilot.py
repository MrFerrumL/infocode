"""Install the reviewed 135-card pilot on the existing VPS from local PowerShell."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import time
from urllib.request import urlopen
import zipfile


POLL_SECONDS = 10
MAX_POLL_FAILURES = 90  # 15 minutes without a connection
MAX_WAIT_SECONDS = 3 * 3600


def run_detached(ssh, options, target, home, payload):
    """Run the install script on the server detached from this SSH session and follow its log.

    A long backup step used to outlive the SSH connection ("Connection reset by peer"), which killed
    the installation halfway. Now the script runs under nohup/setsid and writes INFOCODE_EXIT=<code>
    when it ends; dropped connections while polling are retried.
    """
    script, log = f'{home}/infocode-install.sh', f'{home}/infocode-install.log'
    result = subprocess.run([ssh, '-T', *options, target, f'umask 077; cat > {script}'], input=payload, check=False)
    if result.returncode:
        return result.returncode
    start = (f'rm -f {log}; nohup sudo -n setsid bash -c \'bash {script}; echo INFOCODE_EXIT=$?\' '
             f'> {log} 2>&1 < /dev/null & echo started')
    result = subprocess.run([ssh, '-T', *options, target, start], check=False)
    if result.returncode:
        return result.returncode
    shown, failures, waited = 0, 0, 0
    while waited < MAX_WAIT_SECONDS:
        time.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
        result = subprocess.run([ssh, '-T', *options, target, f'cat {log}'], check=False, capture_output=True)
        if result.returncode:
            failures += 1
            if failures > MAX_POLL_FAILURES:
                print('Lost the connection to the server; the installation may still be running there.', file=sys.stderr)
                return result.returncode
            continue
        failures = 0
        text = (result.stdout or b'').decode('utf-8', 'replace')
        print(text[shown:], end='', flush=True)
        shown = len(text)
        match = re.search(r'INFOCODE_EXIT=(\d+)', text)
        if match:
            subprocess.run([ssh, '-T', *options, target, f'rm -f {script}'], check=False)
            return int(match.group(1))
    print('The installation is taking too long; check the server log ' + log, file=sys.stderr)
    return 124


def validate_archives(source, bundle):
    with zipfile.ZipFile(source) as archive:
        if archive.testzip() is not None:
            raise ValueError('Source archive is corrupt')
        names = archive.namelist()
        if 'infocode/learning/management/commands/import_kompege_pilot.py' not in names or 'infocode/learning/migrations/0014_task_source_reference.py' not in names:
            raise ValueError('Rebuild the current source archive before installing the pilot.')
        for name in names:
            path = PurePosixPath(name)
            if not name.startswith('infocode/') or '..' in path.parts or '\\' in name or path.is_absolute():
                raise ValueError('Unsafe source archive path')
            if path.name.startswith('.env') and path.name not in ('.env.example', '.env.production.example'):
                raise ValueError('Production secrets must not be in a source archive')
            if path.suffix.lower() in ('.pem', '.ppk', '.key', '.db', '.sqlite3') or path.name in ('.local-secret', '.local-credentials.txt'):
                raise ValueError('Private data must not be in a source archive')
    with zipfile.ZipFile(bundle) as archive:
        if archive.getinfo('manifest.json').file_size > 2 * 1024 * 1024:
            raise ValueError('Pilot manifest is too large')
        manifest = json.loads(archive.read('manifest.json'))
        rows = manifest.get('tasks', [])
        if manifest.get('format') != 'infocode-kompege-pilot-v1' or len(rows) != 135 or any(sum(row.get('number') == n for row in rows) != 5 for n in range(1, 28)):
            raise ValueError('Only the 135-card pilot may be installed by this helper.')


def remote_script(user, images=False, number1=False, categories=False, numbers=(), replace=False, variants=False, polyakov=(), notebooks=(), physics=False):
    home = '/root' if user == 'root' else '/home/' + user
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    refresh = f'docker compose --env-file .env.production -f compose.production.yaml run --rm --no-deps -v {home}/kompege-condition-images.zip:/tmp/condition-images.zip:ro web python manage.py refresh_kompege_images /tmp/condition-images.zip < /dev/null\n' if images else ''
    bank = f'docker compose --env-file .env.production -f compose.production.yaml run --rm --no-deps -v {home}/kompege-number1.zip:/tmp/kompege-number1.zip:ro web python manage.py import_kompege_number1 /tmp/kompege-number1.zip --publish < /dev/null\n' if number1 else ''
    if categories:
        if not numbers or any(type(n) is not int or not 1 <= n <= 27 for n in numbers):
            raise ValueError('Select valid category numbers')
        selected = ' '.join(str(n) for n in sorted(set(numbers)))
        replace_flag = ' --replace-existing' if replace else ''
        bank += f'docker compose --env-file .env.production -f compose.production.yaml run --rm --no-deps -v {home}/kompege-categories.zip:/tmp/kompege-categories.zip:ro web python manage.py import_kompege_numbers /tmp/kompege-categories.zip --numbers {selected} --publish{replace_flag} < /dev/null\n'
    # K. Polyakov's bundles: [(numbers, uploaded name)], imported one after another and then removed.
    for selected_numbers, name in polyakov:
        selected = ' '.join(str(n) for n in selected_numbers)
        bank += f'docker compose --env-file .env.production -f compose.production.yaml run --rm --no-deps -v {home}/{name}:/tmp/{name}:ro web python manage.py import_kompege_numbers /tmp/{name} --numbers {selected} --publish < /dev/null\nrm -f {home}/{name}\n'
    if physics:  # the platform's physics content (learning/data/physics), created or updated in place
        bank += 'docker compose --env-file .env.production -f compose.production.yaml run --rm --no-deps web python manage.py seed_physics < /dev/null\n'
    # Colab notebooks for «Конспекты»: a notebook with the same title is updated in place.
    for name in notebooks:
        bank += f'docker compose --env-file .env.production -f compose.production.yaml run --rm --no-deps -v {home}/{name}:/tmp/{name}:ro web python manage.py import_ipynb /tmp/{name} --publish < /dev/null\nrm -f {home}/{name}\n'
    if variants:
        bank += f'docker compose --env-file .env.production -f compose.production.yaml run --rm --no-deps -v {home}/kompege-variants.zip:/tmp/kompege-variants.zip:ro web python manage.py import_kompege_variants /tmp/kompege-variants.zip --publish < /dev/null\n'
    return f'''set -Eeuo pipefail
trap 'printf "Pilot installation stopped at line %s. See the error above; keep the backups.\\n" "$LINENO" >&2' ERR
umask 027
cd /opt/infocode
test -s .env.production
unzip -t {home}/infocode-source-update.zip > /dev/null
printf '[1/6] Backing up the existing database, media and source code...\\n'
docker compose --env-file .env.production -f compose.production.yaml exec -T web python manage.py backup_platform /backups/before-kompege-pilot-{stamp}.zip < /dev/null
mkdir -p /opt/infocode-code-backups
tar -czf /opt/infocode-code-backups/source-before-pilot-{stamp}.tar.gz --exclude=./.env.production --exclude=./uploads --exclude=./logs --exclude=./backups --exclude=./.git -C /opt/infocode .
# Keep the three newest installer backups of each kind (the one just made included).
docker compose --env-file .env.production -f compose.production.yaml exec -T web sh -c 'ls -1t /backups/before-kompege-pilot-*.zip | tail -n +4 | xargs -r rm -f' < /dev/null
ls -1t /opt/infocode-code-backups/source-before-pilot-*.tar.gz | tail -n +4 | xargs -r rm -f
printf '[2/6] Installing updated source and building the application...\\n'
unzip -oq {home}/infocode-source-update.zip -d /opt
docker compose --env-file .env.production -f compose.production.yaml build web < /dev/null
printf '[3/6] Applying the source-reference migration...\\n'
docker compose --env-file .env.production -f compose.production.yaml run --rm migrate < /dev/null
printf '[4/6] Verifying and importing the 135-card pilot...\\n'
docker compose --env-file .env.production -f compose.production.yaml run --rm --no-deps -v {home}/kompege-pilot-135.zip:/tmp/kompege-pilot-135.zip:ro web python manage.py import_kompege_pilot /tmp/kompege-pilot-135.zip --publish --sync-empty-topic-titles < /dev/null
{refresh}
{bank}
docker compose --env-file .env.production -f compose.production.yaml run --rm --no-deps web python manage.py sync_similar_tasks < /dev/null
printf '[5/6] Starting the updated platform...\\n'
docker compose --env-file .env.production -f compose.production.yaml up -d --wait --wait-timeout 120 < /dev/null
printf '[6/6] Checking database, media and application health...\\n'
docker compose --env-file .env.production -f compose.production.yaml exec -T web python manage.py check_content --strict < /dev/null
docker compose --env-file .env.production -f compose.production.yaml exec -T web python deploy/healthcheck.py < /dev/null
printf 'INFOCODE_PILOT_READY\\n'
'''.encode('utf-8')


def main(argv=None):
    project = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='195.209.211.242')
    parser.add_argument('--user', default='ubuntu')
    parser.add_argument('--domain', default='infocode1.ru')
    parser.add_argument('--key', type=Path, default=Path.home() / 'Downloads' / 'privatekey-1152809.pem')
    parser.add_argument('--source', type=Path, default=project.parent / 'infocode-python.zip')
    parser.add_argument('--bundle', type=Path, default=project.parent / 'kompege-pilot-135.zip')
    parser.add_argument('--images', type=Path, help='Optional reviewed high-resolution condition image package')
    parser.add_argument('--number1', type=Path, help='Complete reviewed high-resolution number-1 bank')
    parser.add_argument('--categories', type=Path, help='Complete reviewed high-resolution category snapshots')
    parser.add_argument('--numbers', nargs='+', type=int, help='Exact categories to import from --categories')
    parser.add_argument('--variants', type=Path, help='Reviewed KOMPEGE exam variants package')
    parser.add_argument('--physics', action='store_true', help='Create or update the physics platform content (seed_physics)')
    parser.add_argument('--notebooks', type=Path, nargs='+', default=[], help='Colab notebooks (.ipynb) to publish in «Конспекты»')
    parser.add_argument('--polyakov', type=Path, nargs='+', default=[], help="K. Polyakov's bundles (deploy/prepare_polyakov_bank.py)")
    parser.add_argument('--replace-existing', action='store_true', help='Replace earlier KOMPEGE tasks of --numbers (refused if pupils used them)')
    args = parser.parse_args(argv)
    if args.replace_existing and not args.categories:
        parser.error('--replace-existing requires --categories')
    if bool(args.categories) != bool(args.numbers) or (args.numbers and any(not 1 <= n <= 27 for n in args.numbers)):
        parser.error('--categories requires valid --numbers 1–27')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:-]{0,252}', args.host) or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', args.user):
        parser.error('Invalid SSH host or username')
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]*\.[a-z]{2,63}', args.domain):
        parser.error('Invalid domain')
    ssh, scp = shutil.which('ssh'), shutil.which('scp')
    if not ssh or not scp:
        print('OpenSSH client and scp must be installed.', file=sys.stderr)
        return 2
    key = args.key.expanduser().resolve()
    if not key.is_file() or not args.source.is_file() or not args.bundle.is_file() or (args.images and not args.images.is_file()) or (args.number1 and not args.number1.is_file()) or (args.categories and not args.categories.is_file()) or (args.variants and not args.variants.is_file()) or any(not p.is_file() for p in [*args.polyakov, *args.notebooks]):
        print('SSH key, current source archive or pilot archive is missing.', file=sys.stderr)
        return 2
    try:
        validate_archives(args.source, args.bundle)
        if args.categories:
            with zipfile.ZipFile(args.categories) as archive:
                if archive.getinfo('manifest.json').file_size > 4 * 1024 * 1024:
                    raise ValueError('Category manifest is too large')
                category_manifest = json.loads(archive.read('manifest.json'))
                category_rows = category_manifest.get('tasks', [])
                selected = sorted(set(args.numbers))
                if category_manifest.get('format') not in ('infocode-kompege-numbers-v1', 'infocode-kompege-games-v1', 'infocode-polyakov-doc-v1', 'infocode-polyakov-games-v1') or category_manifest.get('numbers') != selected or not 1 <= len(category_rows) <= 2000 or any(r.get('number') not in selected for r in category_rows):
                    raise ValueError('Expected the complete selected categories')
                if category_manifest.get('source_counts') != {str(n):sum(r['number'] == n for r in category_rows) for n in selected}:
                    raise ValueError('Category snapshot counts do not match')
        polyakov = []
        for index, path in enumerate(args.polyakov, 1):
            with zipfile.ZipFile(path) as archive:
                if archive.getinfo('manifest.json').file_size > 8 * 1024 * 1024:
                    raise ValueError('Polyakov manifest is too large')
                manifest = json.loads(archive.read('manifest.json'))
            rows, selected = manifest.get('tasks', []), manifest.get('numbers')
            if manifest.get('format') not in ('infocode-polyakov-doc-v1', 'infocode-polyakov-games-v1') or not rows or len(rows) > 5000                     or not isinstance(selected, list) or any(type(n) is not int or not 1 <= n <= 27 for n in selected)                     or manifest.get('source_counts') != {str(n): sum(r['number'] == n for r in rows) for n in selected}:
                raise ValueError(f'{path.name}: expected a complete K. Polyakov bundle')
            polyakov.append((sorted(selected), f'polyakov-{index}.zip', path))
        if args.variants:
            with zipfile.ZipFile(args.variants) as archive:
                if archive.getinfo('manifest.json').file_size > 16 * 1024 * 1024:
                    raise ValueError('Variants manifest is too large')
                variants_manifest = json.loads(archive.read('manifest.json'))
                if variants_manifest.get('format') != 'infocode-kompege-variants-v1' or not variants_manifest.get('variants'):
                    raise ValueError('Expected a KOMPEGE variants package')
        if args.number1:
            with zipfile.ZipFile(args.number1) as archive:
                if archive.getinfo('manifest.json').file_size > 4 * 1024 * 1024:
                    raise ValueError('Number-1 manifest is too large')
                bank_manifest = json.loads(archive.read('manifest.json'))
                bank_rows = bank_manifest.get('tasks', [])
                if bank_manifest.get('format') != 'infocode-kompege-number1-v1' or not 1 <= len(bank_rows) <= 2000 or bank_manifest.get('source_count') != len(bank_rows) or any(r.get('number') != 1 for r in bank_rows):
                    raise ValueError('Expected a complete number-1 bank')
        if args.images:
            with zipfile.ZipFile(args.images) as archive:
                image_manifest=json.loads(archive.read('manifest.json'))
                if image_manifest.get('format')!='infocode-condition-images-v1' or len(image_manifest.get('assets',[]))!=135:
                    raise ValueError('Expected the reviewed 135-image condition refresh package')
        options = ['-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                   '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=6', '-o', 'ConnectTimeout=15', '-i', str(key)]
        target = f'{args.user}@{args.host}'
        home = '/root' if args.user == 'root' else '/home/' + args.user
        payload = remote_script(args.user, images=bool(args.images), number1=bool(args.number1), categories=bool(args.categories), numbers=args.numbers or (), replace=args.replace_existing, variants=bool(args.variants), polyakov=[(n, name) for n, name, _ in polyakov], notebooks=[f'notebook-{i}.ipynb' for i, _ in enumerate(args.notebooks, 1)], physics=args.physics)
        result = subprocess.run([ssh, '-T', *options, target, 'bash -n'], input=payload, check=False)
        if result.returncode:
            print('SSH connection or remote syntax check failed; nothing was installed.', file=sys.stderr)
            return result.returncode
        print('Uploading the reviewed 135-card pilot. Keep this terminal open.', flush=True)
        uploads=[(args.source, 'infocode-source-update.zip'), (args.bundle, 'kompege-pilot-135.zip')]
        if args.images: uploads.append((args.images,'kompege-condition-images.zip'))
        if args.number1: uploads.append((args.number1, 'kompege-number1.zip'))
        if args.categories: uploads.append((args.categories, 'kompege-categories.zip'))
        if args.variants: uploads.append((args.variants, 'kompege-variants.zip'))
        uploads += [(path, name) for _, name, path in polyakov]
        uploads += [(path, f'notebook-{i}.ipynb') for i, path in enumerate(args.notebooks, 1)]
        for local, remote in uploads:
            result = subprocess.run([scp, *options, str(local.resolve()), f'{target}:{home}/{remote}'], check=False)
            if result.returncode:
                return result.returncode
        code = run_detached(ssh, options, target, home, payload)
        if code:
            print('Installation did not finish; see the error above. No success is assumed.', file=sys.stderr)
            return code
        with urlopen(f'https://{args.domain}/health/', timeout=20) as response:
            if response.status != 200 or json.load(response).get('status') != 'ok':
                raise ValueError('The public health check did not pass.')
    except KeyboardInterrupt:
        print('Interrupted. Check server state before retrying.', file=sys.stderr)
        return 130
    except (OSError, ValueError, zipfile.BadZipFile, KeyError) as exc:
        print(f'Pilot installation/check failed: {exc}', file=sys.stderr)
        return 2
    print(f'Pilot installed and health checked. Open https://{args.domain}/catalog/ and refresh the page.', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
