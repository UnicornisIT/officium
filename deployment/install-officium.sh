#!/bin/bash
# Idempotent Ubuntu installer/upgrader/recovery entry point for Officium.

set -euo pipefail
umask 027

MODE="${1:-install}"
APP_DIR="${APP_DIR:-/opt/officium}"
APP_USER="${APP_USER:-officium}"
APP_GROUP="${APP_GROUP:-officium}"
VENV_DIR="${VENV_DIR:-$APP_DIR/current/venv}"
SERVICE_NAME="${SERVICE_NAME:-officium}"
MONTHLY_SERVICE="${MONTHLY_SERVICE:-officium-monthly-expenses}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-5000}"
WAITRESS_THREADS="${WAITRESS_THREADS:-4}"
TRUSTED_PROXY="${TRUSTED_PROXY:-127.0.0.1}"
TRUSTED_PROXY_HEADERS="${TRUSTED_PROXY_HEADERS:-x-forwarded-for x-forwarded-host x-forwarded-proto x-forwarded-port}"
APP_TIMEZONE="${APP_TIMEZONE:-Europe/Moscow}"
DB_ENGINE="${DB_ENGINE:-mysql}"
DB_NAME="${DB_NAME:-officium}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/officium}"
STATE_DIR="${STATE_DIR:-/var/lib/officium}"
UPDATE_SOCKET="${UPDATE_SOCKET:-/run/officium/updater.sock}"
REPOSITORY="${REPOSITORY:-UnicornisIT/officium}"
RELEASE_TAG="${RELEASE_TAG:-}"
ENV_FILE_SOURCE="${ENV_FILE_SOURCE:-}"
SERVER_NAME="${SERVER_NAME:-_}"
ROLLBACK_RELEASE="${ROLLBACK_RELEASE:-}"
ENABLE_NGINX="${ENABLE_NGINX:-false}"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
info() { printf '==> %s\n' "$*"; }
valid_name() { [[ "$1" =~ ^[A-Za-z0-9_][A-Za-z0-9_.@-]{0,127}$ ]]; }
render() {
    sed \
        -e "s|@@APP_ROOT@@|$APP_DIR|g" \
        -e "s|@@APP_USER@@|$APP_USER|g" \
        -e "s|@@APP_GROUP@@|$APP_GROUP|g" \
        -e "s|@@VENV_DIR@@|$VENV_DIR|g" \
        -e "s|@@HOST@@|$HOST|g" \
        -e "s|@@PORT@@|$PORT|g" \
        -e "s|@@THREADS@@|$WAITRESS_THREADS|g" \
        -e "s|@@TRUSTED_PROXY@@|$TRUSTED_PROXY|g" \
        -e "s|@@TRUSTED_PROXY_HEADERS@@|$TRUSTED_PROXY_HEADERS|g" \
        -e "s|@@APP_TIMEZONE@@|$APP_TIMEZONE|g" \
        -e "s|@@MONTHLY_SERVICE@@|$MONTHLY_SERVICE|g" \
        -e "s|@@SERVER_NAME@@|$SERVER_NAME|g" \
        "$1" >"$2"
}
set_env_value() {
    env_path="$1"
    env_key="$2"
    env_value="$3"
    if grep -q "^${env_key}=" "$env_path"; then
        sed -i "s|^${env_key}=.*|${env_key}=${env_value}|" "$env_path"
    else
        printf '%s=%s\n' "$env_key" "$env_value" >>"$env_path"
    fi
}
configure_managed_env() {
    managed_env="$1"
    set_env_value "$managed_env" SERVER_UPDATE_APP_DIR "$APP_DIR/current"
    set_env_value "$managed_env" SERVER_UPDATE_STATUS_PATH "$STATE_DIR/server-update-status.json"
    set_env_value "$managed_env" SERVER_UPDATE_SOCKET "$UPDATE_SOCKET"
}
seal_release() {
    release_path="$1"
    expected_tag="$2"
    releases_real=$(realpath -e "$(dirname "$release_path")")
    release_real=$(realpath -e "$release_path")
    [ "$(dirname "$release_real")" = "$releases_real" ] || fail "Release escaped the managed releases directory."
    release_commit=$(tr -d '\r\n' <"$release_real/.officium-release-ready")
    [[ "$release_commit" =~ ^[0-9a-f]{40,64}$ ]] || fail "Release commit marker is invalid."
    remote_commit=$(git ls-remote --exit-code "https://github.com/$REPOSITORY.git" \
        "refs/tags/$expected_tag^{}" "refs/tags/$expected_tag" | \
        awk '$2 ~ /\^\{\}$/ {peeled=$1} NR==1 {direct=$1} END {print peeled ? peeled : direct}')
    [ "$remote_commit" = "$release_commit" ] || fail "Release commit no longer matches the remote tag."

    # Remove APP_USER write access before trusting any tracked content. Git
    # replace refs and external diff drivers are disabled for the root check.
    chown -hR "root:$APP_GROUP" "$release_real"
    chmod -R a-w "$release_real"
    git --no-replace-objects -c "safe.directory=$release_real" -c core.hooksPath=/dev/null \
        -C "$release_real" rev-parse --verify "$release_commit^{commit}" >/dev/null
    git --no-replace-objects -c "safe.directory=$release_real" -c core.hooksPath=/dev/null \
        -C "$release_real" diff --no-ext-diff --quiet "$release_commit" -- || \
        fail "Tracked release files differ from the verified commit."
    for privileged_file in \
        deployment/officium-update-runner \
        deployment/officium-update-client \
        deployment/deploy-release; do
        candidate="$release_real/$privileged_file"
        [ -f "$candidate" ] && [ ! -L "$candidate" ] || fail "Privileged bundle file is unsafe: $privileged_file"
        [ "$(stat -c '%u' "$candidate")" -eq 0 ] || fail "Privileged bundle is not root-owned."
        mode=$(stat -c '%a' "$candidate")
        [ $((8#$mode & 8#222)) -eq 0 ] || fail "Privileged bundle is writable."
    done
}

[ "$(id -u)" -eq 0 ] || fail "Run with sudo/root."
case "$MODE" in install|upgrade|recover|preflight) ;; *) fail "Usage: $0 install|upgrade|recover|preflight" ;; esac
for name in "$APP_USER" "$APP_GROUP" "$SERVICE_NAME" "$MONTHLY_SERVICE"; do
    valid_name "$name" || fail "Unsafe user, group, or service name: $name"
done
[[ "$REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || fail "Invalid REPOSITORY."
[[ "$SERVER_NAME" =~ ^[A-Za-z0-9._-]+$ ]] || fail "Invalid SERVER_NAME."
[[ "$DB_NAME" =~ ^[A-Za-z0-9_$][A-Za-z0-9_$-]{0,63}$ ]] || fail "Invalid DB_NAME."
[[ "$PORT" =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || fail "Invalid PORT."
[[ "$WAITRESS_THREADS" =~ ^[0-9]+$ ]] && [ "$WAITRESS_THREADS" -ge 1 ] && [ "$WAITRESS_THREADS" -le 64 ] || fail "Invalid WAITRESS_THREADS."
[[ "$HOST" = "127.0.0.1" || "$HOST" = "::1" ]] || fail "Waitress must bind to loopback."
[[ "$TRUSTED_PROXY" = "127.0.0.1" || "$TRUSTED_PROXY" = "::1" ]] || fail "TRUSTED_PROXY must be an explicit loopback proxy."
[[ "$TRUSTED_PROXY_HEADERS" =~ ^[a-z-]+([[:space:]][a-z-]+)*$ ]] || fail "Invalid proxy header list."
[[ "$APP_TIMEZONE" =~ ^[A-Za-z_+-]+(/[A-Za-z0-9_+-]+)+$|^UTC$ ]] || fail "Invalid APP_TIMEZONE."
case "$DB_ENGINE" in mysql|sqlite) ;; *) fail "DB_ENGINE must be mysql or sqlite." ;; esac
for path in "$APP_DIR" "$VENV_DIR" "$BACKUP_DIR" "$STATE_DIR" "$UPDATE_SOCKET"; do
    [[ "$path" = /* && "$path" != *".."* ]] || fail "Unsafe non-absolute path: $path"
    [[ "$path" =~ ^/[A-Za-z0-9_./@+-]+$ ]] || fail "Path contains unsupported characters: $path"
done
case "$VENV_DIR" in "$APP_DIR/current/venv") VENV_NAME=venv ;; "$APP_DIR/current/.venv") VENV_NAME=.venv ;; *) fail "VENV_DIR must be current/venv or current/.venv for atomic releases." ;; esac

info "Preflight"
grep -qi '^ID=ubuntu' /etc/os-release || fail "This installer supports Ubuntu; use a tested VM for another OS."
for command in git python3 systemctl systemd-analyze runuser curl; do
    command -v "$command" >/dev/null 2>&1 || fail "Missing command: $command"
done
python3 -m venv --help >/dev/null 2>&1 || fail "python3-venv is unavailable."
id "$APP_USER" >/dev/null 2>&1 || fail "Create the dedicated APP_USER before installation: $APP_USER"
getent group "$APP_GROUP" >/dev/null 2>&1 || fail "Create APP_GROUP before installation: $APP_GROUP"
id -nG "$APP_USER" | tr ' ' '\n' | grep -qx "$APP_GROUP" || fail "APP_USER is not a member of APP_GROUP."
available_kb=$(df -Pk "$(dirname "$APP_DIR")" | awk 'NR==2 {print $4}')
[[ "$available_kb" =~ ^[0-9]+$ ]] && [ "$available_kb" -ge 1048576 ] || fail "At least 1 GiB free space is required."
if [ "$MODE" != recover ]; then
    [[ "$RELEASE_TAG" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$ ]] || fail "Set exact RELEASE_TAG."
    [[ "$RELEASE_TAG" != *..* && "$RELEASE_TAG" != *@\{* && "$RELEASE_TAG" != *.lock ]] || fail "Unsafe RELEASE_TAG."
    git ls-remote --exit-code "https://github.com/$REPOSITORY.git" "refs/tags/$RELEASE_TAG" >/dev/null || fail "Release tag does not exist in configured repository."
fi
if [ "$MODE" = install ] || [ "$MODE" = preflight ]; then
    [ -n "$ENV_FILE_SOURCE" ] && [ -f "$ENV_FILE_SOURCE" ] || fail "Set ENV_FILE_SOURCE to a prepared .env file."
    grep -Eq '^SECRET_KEY=.{32,}$' "$ENV_FILE_SOURCE" || fail "ENV file needs a non-empty SECRET_KEY of at least 32 characters."
    grep -Eq "^APP_TIMEZONE=$APP_TIMEZONE$" "$ENV_FILE_SOURCE" || fail "ENV file must explicitly match APP_TIMEZONE."
    grep -Eq "^DB_ENGINE=$DB_ENGINE$" "$ENV_FILE_SOURCE" || fail "ENV file must explicitly match DB_ENGINE."
    grep -Eq "^DB_NAME=$DB_NAME$" "$ENV_FILE_SOURCE" || fail "ENV file must explicitly match DB_NAME."
    if [ "$DB_ENGINE" = sqlite ]; then
        database_url=$(sed -n 's/^DATABASE_URL=//p' "$ENV_FILE_SOURCE" | tail -n 1)
        sqlite_path=$(sed -n 's/^SQLITE_PATH=//p' "$ENV_FILE_SOURCE" | tail -n 1)
        [ -z "$database_url" ] || fail "Managed SQLite installs must use SQLITE_PATH, not DATABASE_URL."
        case "$sqlite_path" in
            "$APP_DIR/shared/"*) ;;
            *) fail "SQLITE_PATH must be absolute and stored below $APP_DIR/shared/." ;;
        esac
    fi
fi
if [ "$DB_ENGINE" = mysql ]; then
    command -v mysqldump >/dev/null 2>&1 || fail "Install mysql-client (mysqldump)."
fi
[ "$MODE" != preflight ] || { info "Preflight PASS; no changes made."; exit 0; }

if [ "$MODE" = recover ]; then
    [ -n "$ROLLBACK_RELEASE" ] || fail "Set ROLLBACK_RELEASE to a directory under $APP_DIR/releases."
    rollback_real=$(realpath -e "$ROLLBACK_RELEASE")
    releases_real=$(realpath -e "$APP_DIR/releases")
    [ "$(dirname "$rollback_real")" = "$releases_real" ] || fail "Rollback target is outside releases."
    [ -f "$rollback_real/.officium-release-ready" ] || fail "Rollback release is incomplete."
    previous=$(realpath -e "$APP_DIR/current")
    ln -s "$rollback_real" "$APP_DIR/.current.rollback"
    mv -Tf "$APP_DIR/.current.rollback" "$APP_DIR/current"
    if ! systemctl restart "$SERVICE_NAME" || ! systemctl is-active --quiet "$SERVICE_NAME"; then
        ln -s "$previous" "$APP_DIR/.current.restore"
        mv -Tf "$APP_DIR/.current.restore" "$APP_DIR/current"
        systemctl restart "$SERVICE_NAME"
        fail "Rollback code failed health activation; previous code link restored. Database was not modified."
    fi
    info "Code rolled back. Database restore is intentionally separate; see docs/DEPLOYMENT.md."
    exit 0
fi

# Legacy in-place checkouts (including the reported /opt/officium v0.0.7
# installation) are migrated without checking out over the running tree. The
# old directory and unit are retained until the new service passes smoke tests.
if [ "$MODE" = upgrade ] && { [ -d "$APP_DIR/.git" ] || [ -f "$APP_DIR/.git" ]; } && [ ! -L "$APP_DIR/current" ]; then
    info "Migrating legacy in-place checkout to managed release directories"
    [ -f "$APP_DIR/.env" ] || fail "Legacy checkout has no .env; refusing an unsafe migration."
    grep -Eq '^APP_TIMEZONE=.+$' "$APP_DIR/.env" || fail "Add an explicit APP_TIMEZONE before migration."
    git -C "$APP_DIR" diff --quiet --ignore-submodules -- || fail "Tracked local changes exist; they are preserved, but migration requires owner review."
    git -C "$APP_DIR" diff --cached --quiet --ignore-submodules -- || fail "Staged local changes exist; migration requires owner review."

    APP_ROOT="$APP_DIR" STATE_DIR="$STATE_DIR" BACKUP_DIR="$BACKUP_DIR" UPDATE_SOCKET="$UPDATE_SOCKET" \
        /bin/sh "$SCRIPT_DIR/install-officium-updater.sh" "$APP_USER" "$APP_GROUP"
    sed -i \
        -e "s|^APP_DIR=.*|APP_DIR=$APP_DIR|" \
        -e "s|^APP_USER=.*|APP_USER=$APP_USER|" \
        -e "s|^APP_GROUP=.*|APP_GROUP=$APP_GROUP|" \
        -e "s|^BACKUP_DIR=.*|BACKUP_DIR=$BACKUP_DIR|" \
        -e "s|^REPOSITORY=.*|REPOSITORY=$REPOSITORY|" \
        -e "s|^SERVICE_NAME=.*|SERVICE_NAME=$SERVICE_NAME|" \
        /etc/officium/updater.env
    /usr/local/sbin/officium-update-runner --backup-only legacy-migration

    managed_root="${APP_DIR}.managed.$$"
    legacy_root="${APP_DIR}.legacy.$(date -u +%Y%m%dT%H%M%SZ)"
    failed_root="${APP_DIR}.failed.$(date -u +%Y%m%dT%H%M%SZ)"
    [ ! -e "$managed_root" ] && [ ! -e "$legacy_root" ] || fail "Migration staging path already exists."
    install -d -o root -g "$APP_GROUP" -m 0750 "$managed_root"
    install -d -o root -g "$APP_GROUP" -m 0750 "$managed_root/releases"
    install -d -o root -g "$APP_GROUP" -m 0750 "$managed_root/shared"
    install -o root -g "$APP_GROUP" -m 0640 "$APP_DIR/.env" "$managed_root/shared/.env"
    configure_managed_env "$managed_root/shared/.env"
    install -d -o "$APP_USER" -g "$APP_GROUP" -m 0750 "$managed_root/shared/instance"

    # SQLite files tied to the old checkout would otherwise be left behind or
    # replaced by a new empty database after the atomic directory migration.
    python3 - "$APP_DIR/.env" "$APP_DIR" "$managed_root/shared/.env" "$managed_root/shared/officium.sqlite" <<'PY'
import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import unquote

source_env, source_root, target_env, target_database = map(Path, sys.argv[1:])
lines = source_env.read_text(encoding='utf-8').splitlines()
values = {}
for raw_line in lines:
    line = raw_line.strip()
    if line and not line.startswith('#') and '=' in line:
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip().strip('"\'')
database_url = values.get('DATABASE_URL', '')
engine = values.get('DB_ENGINE', 'mysql').lower()
if database_url.startswith('sqlite:///'):
    raw_path = unquote(database_url[len('sqlite:///'):].split('?', 1)[0])
    source_database = Path(raw_path)
    if not source_database.is_absolute():
        source_database = source_root / source_database
elif not database_url and engine == 'sqlite':
    source_database = Path(values.get('SQLITE_PATH', 'dev.db'))
    if not source_database.is_absolute():
        source_database = source_root / source_database
else:
    raise SystemExit(0)
if not source_database.is_file():
    raise SystemExit(f'Legacy SQLite database does not exist: {source_database}')
target_database.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(source_database) as source, sqlite3.connect(target_database) as target:
    source.backup(target)
rewritten = []
seen = set()
for raw_line in lines:
    key = raw_line.split('=', 1)[0].strip() if '=' in raw_line else ''
    if key == 'DATABASE_URL':
        rewritten.append('DATABASE_URL=')
        seen.add(key)
    elif key == 'DB_ENGINE':
        rewritten.append('DB_ENGINE=sqlite')
        seen.add(key)
    elif key == 'SQLITE_PATH':
        rewritten.append(f'SQLITE_PATH={target_database}')
        seen.add(key)
    else:
        rewritten.append(raw_line)
for key, value in (
    ('DATABASE_URL', ''), ('DB_ENGINE', 'sqlite'), ('SQLITE_PATH', str(target_database))
):
    if key not in seen:
        rewritten.append(f'{key}={value}')
target_env.write_text('\n'.join(rewritten) + '\n', encoding='utf-8')
PY
    if [ -f "$managed_root/shared/officium.sqlite" ]; then
        chown "$APP_USER:$APP_GROUP" "$managed_root/shared/officium.sqlite"
        chmod 0640 "$managed_root/shared/officium.sqlite"
        chown root:"$APP_GROUP" "$managed_root/shared/.env"
        chmod 0640 "$managed_root/shared/.env"
    fi

    stage_file=$(mktemp /tmp/officium-legacy-stage.XXXXXX)
    result_file=$(mktemp /tmp/officium-legacy-result.XXXXXX)
    build_root=$(mktemp -d "$managed_root/releases/.build-legacy.XXXXXX")
    chown "$APP_USER:$APP_GROUP" "$build_root"
    chmod 0750 "$build_root"
    chown "$APP_USER:$APP_GROUP" "$stage_file" "$result_file"
    chmod 0600 "$stage_file" "$result_file"
    runuser -u "$APP_USER" -- /usr/local/lib/officium/deploy-release \
        --release "$RELEASE_TAG" --repository "$REPOSITORY" \
        --releases-dir "$build_root" --current-link "$APP_DIR/current" \
        --environment-file "$managed_root/shared/.env" --stage-file "$stage_file" \
        --result-file "$result_file" --venv-name "$VENV_NAME"
    built_release=$(sed -n '1p' "$result_file")
    release_name=$(basename "$built_release")
    staged_release="$managed_root/releases/$release_name"
    [ ! -e "$staged_release" ] && [ ! -L "$staged_release" ] || fail "Managed release path already exists."
    mv -- "$built_release" "$staged_release"
    # The release moves twice more (here and with the whole managed tree); the
    # venv scripts are pointed at the final path before the tree is sealed.
    /usr/local/sbin/officium-update-runner --relocate-venv         "$staged_release" "$built_release" "$APP_DIR/releases/$release_name"
    rm -rf -- "$build_root"
    rm -f "$stage_file" "$result_file"
    [ -f "$staged_release/.officium-release-ready" ] || fail "Managed release build is incomplete."
    seal_release "$staged_release" "$RELEASE_TAG"
    ln -s "$APP_DIR/releases/$release_name" "$managed_root/current"

    old_unit_backup=""
    if [ -f "/etc/systemd/system/$SERVICE_NAME.service" ]; then
        old_unit_backup="$BACKUP_DIR/$SERVICE_NAME.service.legacy"
        cp -a "/etc/systemd/system/$SERVICE_NAME.service" "$old_unit_backup"
    fi
    systemctl stop "$SERVICE_NAME.service"
    mv -- "$APP_DIR" "$legacy_root"
    mv -- "$managed_root" "$APP_DIR"
    release_script_dir="$APP_DIR/releases/$release_name/deployment"
    [ -d "$release_script_dir" ] || fail "Target release has no deployment bundle."
    if ! {
        /usr/local/sbin/officium-update-runner --verify-release "$APP_DIR/releases/$release_name" &&
        render "$release_script_dir/officium.service.in" "/etc/systemd/system/$SERVICE_NAME.service" &&
        render "$release_script_dir/officium-monthly-expenses.service" "/etc/systemd/system/$MONTHLY_SERVICE.service" &&
        render "$release_script_dir/officium-monthly-expenses.timer" "/etc/systemd/system/$MONTHLY_SERVICE.timer" &&
        systemd-analyze verify "/etc/systemd/system/$SERVICE_NAME.service" "/etc/systemd/system/$MONTHLY_SERVICE.service" "/etc/systemd/system/$MONTHLY_SERVICE.timer" &&
        systemctl daemon-reload && systemctl restart "$SERVICE_NAME.service" &&
        systemctl is-active --quiet "$SERVICE_NAME.service";
    }; then
        systemctl stop "$SERVICE_NAME.service" || true
        mv -- "$APP_DIR" "$failed_root"
        mv -- "$legacy_root" "$APP_DIR"
        if [ -n "$old_unit_backup" ]; then cp -a "$old_unit_backup" "/etc/systemd/system/$SERVICE_NAME.service"; fi
        systemctl daemon-reload
        systemctl restart "$SERVICE_NAME.service"
        fail "Managed service failed; legacy checkout and unit were restored. Database backup is in $BACKUP_DIR."
    fi

    APP_ROOT="$APP_DIR" STATE_DIR="$STATE_DIR" BACKUP_DIR="$BACKUP_DIR" UPDATE_SOCKET="$UPDATE_SOCKET" \
        /bin/sh "$release_script_dir/install-officium-updater.sh" "$APP_USER" "$APP_GROUP"
    sed -i \
        -e "s|^APP_DIR=.*|APP_DIR=$APP_DIR/current|" \
        -e "s|^APP_USER=.*|APP_USER=$APP_USER|" \
        -e "s|^APP_GROUP=.*|APP_GROUP=$APP_GROUP|" \
        -e "s|^BACKUP_DIR=.*|BACKUP_DIR=$BACKUP_DIR|" \
        -e "s|^REPOSITORY=.*|REPOSITORY=$REPOSITORY|" \
        -e "s|^RELEASES_DIR=.*|RELEASES_DIR=$APP_DIR/releases|" \
        -e "s|^SERVICE_NAME=.*|SERVICE_NAME=$SERVICE_NAME|" \
        -e "s|^STATUS_FILE=.*|STATUS_FILE=$STATE_DIR/server-update-status.json|" \
        -e "s|^STALE_MINUTES=.*|STALE_MINUTES=120|" \
        -e "s|^HEALTHCHECK_URL=.*|HEALTHCHECK_URL=http://$HOST:$PORT/login|" \
        -e "s|^VENV_NAME=.*|VENV_NAME=$VENV_NAME|" \
        /etc/officium/updater.env
    systemctl enable --now "$MONTHLY_SERVICE.timer" officium-updater.socket
    runuser -u "$APP_USER" -- /usr/local/bin/officium-update-client integration_check
    /bin/bash "$release_script_dir/post-deploy-smoke.sh" --service "$SERVICE_NAME" --app-dir "$APP_DIR/current" --url "http://$HOST:$PORT/login" --timer "$MONTHLY_SERVICE.timer"
    info "Legacy migration PASS. Preserved previous checkout at $legacy_root; remove only after backup/rollback review."
    exit 0
fi

info "Creating managed directories"
install -d -o root -g "$APP_GROUP" -m 0750 "$APP_DIR"
install -d -o root -g "$APP_GROUP" -m 0750 "$APP_DIR/releases"
install -d -o root -g "$APP_GROUP" -m 0750 "$APP_DIR/shared"
install -d -o root -g root -m 0750 "$BACKUP_DIR"
install -d -o root -g "$APP_GROUP" -m 0750 "$STATE_DIR"

if [ "$MODE" = install ]; then
    [ ! -e "$APP_DIR/current" ] || fail "current already exists; use upgrade or a new APP_DIR."
    install -o root -g "$APP_GROUP" -m 0640 "$ENV_FILE_SOURCE" "$APP_DIR/shared/.env"
    configure_managed_env "$APP_DIR/shared/.env"
    install -d -o "$APP_USER" -g "$APP_GROUP" -m 0750 "$APP_DIR/shared/instance"
elif [ -f "$APP_DIR/shared/.env" ]; then
    config_backup="$BACKUP_DIR/app.env.before-installer.$(date -u +%Y%m%dT%H%M%SZ)"
    install -o root -g root -m 0600 "$APP_DIR/shared/.env" "$config_backup"
    configure_managed_env "$APP_DIR/shared/.env"
fi

info "Installing restricted updater"
APP_ROOT="$APP_DIR" STATE_DIR="$STATE_DIR" BACKUP_DIR="$BACKUP_DIR" UPDATE_SOCKET="$UPDATE_SOCKET" \
    /bin/sh "$SCRIPT_DIR/install-officium-updater.sh" "$APP_USER" "$APP_GROUP"
sed -i \
    -e "s|^APP_DIR=.*|APP_DIR=$APP_DIR/current|" \
    -e "s|^APP_USER=.*|APP_USER=$APP_USER|" \
    -e "s|^APP_GROUP=.*|APP_GROUP=$APP_GROUP|" \
    -e "s|^BACKUP_DIR=.*|BACKUP_DIR=$BACKUP_DIR|" \
    -e "s|^REPOSITORY=.*|REPOSITORY=$REPOSITORY|" \
    -e "s|^RELEASES_DIR=.*|RELEASES_DIR=$APP_DIR/releases|" \
    -e "s|^HEALTHCHECK_URL=.*|HEALTHCHECK_URL=http://$HOST:$PORT/login|" \
    -e "s|^SERVICE_NAME=.*|SERVICE_NAME=$SERVICE_NAME|" \
    -e "s|^STATUS_FILE=.*|STATUS_FILE=$STATE_DIR/server-update-status.json|" \
    -e "s|^STALE_MINUTES=.*|STALE_MINUTES=120|" \
    -e "s|^VENV_NAME=.*|VENV_NAME=$VENV_NAME|" \
    /etc/officium/updater.env

if [ "$MODE" = upgrade ]; then
    /usr/local/sbin/officium-update-runner --check
    /usr/local/sbin/officium-update-runner --start "$RELEASE_TAG"
    info "Upgrade scheduled. Follow: journalctl -fu officium-updater.service"
    exit 0
fi

info "Building exact release without touching an active checkout"
stage_file=$(mktemp /tmp/officium-install-stage.XXXXXX)
result_file=$(mktemp /tmp/officium-install-result.XXXXXX)
build_root=$(mktemp -d "$APP_DIR/releases/.build-install.XXXXXX")
trap 'rm -rf -- "$build_root"; rm -f "$stage_file" "$result_file"' EXIT
chown "$APP_USER:$APP_GROUP" "$build_root"
chmod 0750 "$build_root"
chown "$APP_USER:$APP_GROUP" "$stage_file" "$result_file"
chmod 0600 "$stage_file" "$result_file"
runuser -u "$APP_USER" -- /usr/local/lib/officium/deploy-release \
    --release "$RELEASE_TAG" --repository "$REPOSITORY" \
    --releases-dir "$build_root" --current-link "$APP_DIR/current" \
    --environment-file "$APP_DIR/shared/.env" --stage-file "$stage_file" \
    --result-file "$result_file" --venv-name "$VENV_NAME"
build_release=$(sed -n '1p' "$result_file")
release_commit=$(sed -n '2p' "$result_file")
[ -d "$build_release" ] && [ ! -L "$build_release" ] || fail "Release build did not produce a safe directory."
release_path="$APP_DIR/releases/$(basename "$build_release")"
if [ -e "$release_path" ] || [ -L "$release_path" ]; then
    [ -d "$release_path" ] && [ ! -L "$release_path" ] || fail "Existing release path is unsafe."
    [ "$(tr -d '\r\n' <"$release_path/.officium-release-ready")" = "$release_commit" ] || \
        fail "Existing release commit marker differs."
else
    mv -- "$build_release" "$release_path"
    /usr/local/sbin/officium-update-runner --relocate-venv "$release_path" "$build_release" "$release_path"
fi
rm -rf -- "$build_root"
/usr/local/sbin/officium-update-runner --verify-release "$release_path"
seal_release "$release_path" "$RELEASE_TAG"
ln -s "$release_path" "$APP_DIR/.current.initial"
mv -Tf "$APP_DIR/.current.initial" "$APP_DIR/current"

if ! (
    info "Rendering systemd units"
    render "$SCRIPT_DIR/officium.service.in" "/etc/systemd/system/$SERVICE_NAME.service"
    render "$SCRIPT_DIR/officium-monthly-expenses.service" "/etc/systemd/system/$MONTHLY_SERVICE.service"
    render "$SCRIPT_DIR/officium-monthly-expenses.timer" "/etc/systemd/system/$MONTHLY_SERVICE.timer"
    chmod 0644 "/etc/systemd/system/$SERVICE_NAME.service" "/etc/systemd/system/$MONTHLY_SERVICE.service" "/etc/systemd/system/$MONTHLY_SERVICE.timer"
    systemd-analyze verify "/etc/systemd/system/$SERVICE_NAME.service" "/etc/systemd/system/$MONTHLY_SERVICE.service" "/etc/systemd/system/$MONTHLY_SERVICE.timer"

    if [ "$ENABLE_NGINX" = true ]; then
        command -v nginx >/dev/null 2>&1 || fail "ENABLE_NGINX=true but nginx is not installed."
        render "$SCRIPT_DIR/nginx-officium.conf.in" "/etc/nginx/sites-available/$SERVICE_NAME"
        ln -sfn "/etc/nginx/sites-available/$SERVICE_NAME" "/etc/nginx/sites-enabled/$SERVICE_NAME"
        nginx -t
    fi

    systemctl daemon-reload
    systemctl enable --now "$SERVICE_NAME.service" "$MONTHLY_SERVICE.timer" officium-updater.socket
    systemctl is-active --quiet "$SERVICE_NAME.service" || fail "Application service is not active."
    systemctl is-active --quiet "$MONTHLY_SERVICE.timer" || fail "Monthly timer is not active."
    if [ "$ENABLE_NGINX" = true ]; then
        systemctl reload nginx
    fi
    runuser -u "$APP_USER" -- /usr/local/bin/officium-update-client integration_check
    /bin/bash "$SCRIPT_DIR/post-deploy-smoke.sh" --service "$SERVICE_NAME" --app-dir "$APP_DIR/current" --url "http://$HOST:$PORT/login" --timer "$MONTHLY_SERVICE.timer"
); then
    systemctl disable --now "$SERVICE_NAME.service" "$MONTHLY_SERVICE.timer" >/dev/null 2>&1 || true
    current_target=$(realpath -e "$APP_DIR/current" 2>/dev/null || true)
    release_target=$(realpath -e "$release_path" 2>/dev/null || true)
    if [ -L "$APP_DIR/current" ] && [ -n "$release_target" ] && [ "$current_target" = "$release_target" ]; then
        unlink "$APP_DIR/current"
    fi
    if [ -L "/etc/nginx/sites-enabled/$SERVICE_NAME" ] && \
            [ "$(readlink "/etc/nginx/sites-enabled/$SERVICE_NAME")" = "/etc/nginx/sites-available/$SERVICE_NAME" ]; then
        unlink "/etc/nginx/sites-enabled/$SERVICE_NAME"
        nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
    fi
    fail "Initial activation failed; no current release is active. Fix the reported cause and rerun install."
fi
info "Installation completed without changing production outside the selected APP_DIR/services."
