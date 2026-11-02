#!/usr/bin/env bash
# Release reviewed source without migrations; use the main installer for schema changes.
set -Eeuo pipefail
umask 027
ROOT=/opt/infocode
ARCHIVE=${1:?Usage: sudo bash deploy/release.sh /absolute/source.tar.gz}
test "$(id -u)" = 0
test -s "$ROOT/.env.production"
exec 9>"$ROOT/.deploy.lock"
flock -n 9 || { echo 'Another deployment is running.' >&2; exit 1; }
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
STAGE="/opt/infocode-releases/$STAMP"
mkdir -p "$STAGE/source"
DC=(docker compose --project-directory "$ROOT" --env-file "$ROOT/.env.production" -f "$ROOT/compose.production.yaml")
OLD_IMAGE=$(docker inspect -f '{{.Image}}' "$("${DC[@]}" ps --quiet web)")
CANDIDATE="infocode-app:release-$STAMP"
ACTIVATED=0
printf '%s\n' "$STAMP" > "$ROOT/.deploy-in-progress"

finish() {
    result=$?
    trap - EXIT
    if (( result != 0 && ACTIVATED == 1 )); then
        echo 'Release failed; restoring the previous source and application image.' >&2
        tar xzf "$STAGE/previous-source.tar.gz" -C "$ROOT" || true
        docker tag "$OLD_IMAGE" infocode-app:local || true
        "${DC[@]}" up -d --no-deps --wait --wait-timeout 120 web maintenance || true
        "${DC[@]}" exec -T caddy caddy reload --config /etc/caddy/Caddyfile || true
    fi
    rm -f "$ROOT/.deploy-in-progress"
    echo "INFOCODE_RELEASE_EXIT=$result"
    exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Check before extracting: source archives must not replace secrets or data.
python3 - "$ARCHIVE" "$STAGE/source" <<'PY'
import pathlib, sys, tarfile
with tarfile.open(sys.argv[1], 'r:gz') as source:
    for entry in source.getmembers():
        path = pathlib.PurePosixPath(entry.name)
        if (path.is_absolute() or '..' in path.parts or '\\' in entry.name
            or not entry.isfile() or not path.parts
            or path.parts[0] in ('uploads', 'models', 'logs', 'backups', '.git')
            or path.name in ('.local-secret', '.local-credentials.txt')
            or (path.name.startswith('.env') and path.name not in ('.env.example', '.env.production.example'))
            or path.suffix.lower() in ('.pem', '.ppk', '.key', '.sqlite3', '.db')):
            raise SystemExit('Unsafe source archive member')
    source.extractall(sys.argv[2], filter='data')
PY
chmod -R a+rX,go-w "$STAGE/source"
docker build --build-arg REQUIREMENTS=requirements-postgres.txt -t "$CANDIDATE" "$STAGE/source"
printf '{"services":{"web":{"image":"%s"}}}\n' "$CANDIDATE" > "$STAGE/candidate.json"
CHECK=("${DC[@]}" -f "$STAGE/candidate.json")
"${CHECK[@]}" run --rm --no-deps web python manage.py check --deploy --fail-level WARNING
# Refuse automatic schema changes: the rollback below restores code, never pupil data.
"${CHECK[@]}" run --rm --no-deps web python manage.py migrate --check
docker run --rm --env-file "$ROOT/.env.production" -v "$STAGE/source/deploy:/etc/caddy:ro" caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile

"${DC[@]}" exec -T web python manage.py backup_platform "/backups/before-release-$STAMP.zip"
tar czf "$STAGE/previous-source.tar.gz" --exclude=./.env.production --exclude=./uploads --exclude=./logs --exclude=./backups --exclude=./models --exclude=./.git --exclude=./.deploy.lock --exclude=./.deploy-in-progress -C "$ROOT" .
echo "Verified data backup: before-release-$STAMP.zip"
ACTIVATED=1
tar xzf "$ARCHIVE" -C "$ROOT"
chmod -R a+rX,go-w "$ROOT/learning" "$ROOT/config" "$ROOT/templates" "$ROOT/static" "$ROOT/deploy"
docker tag "$CANDIDATE" infocode-app:local
"${DC[@]}" up -d --no-deps --wait --wait-timeout 120 web maintenance
"${DC[@]}" exec -T caddy caddy reload --config /etc/caddy/Caddyfile
"${DC[@]}" exec -T web python deploy/healthcheck.py
"${DC[@]}" exec -T web python manage.py check_content --strict
python3 "$ROOT/deploy/smoke_check.py"

systemd-analyze verify "$ROOT/deploy/infocode-watchdog.service" "$ROOT/deploy/infocode-watchdog.timer"
install -m 0644 "$ROOT/deploy/infocode-watchdog.service" /etc/systemd/system/infocode-watchdog.service
install -m 0644 "$ROOT/deploy/infocode-watchdog.timer" /etc/systemd/system/infocode-watchdog.timer
systemctl daemon-reload
systemctl enable --now infocode-watchdog.timer
# Retain three release data copies only after the new version passed all probes.
# Daily and manually named archives belong to their own retention policies.
"${DC[@]}" exec -T web python - <<'PY'
from pathlib import Path
archives = sorted((p for p in Path('/backups').glob('before-release-????????T??????Z.zip')
                   if p.is_file() and not p.is_symlink()), reverse=True)
for old in archives[3:]:
    old.unlink()
PY
echo INFOCODE_RELEASE_READY
