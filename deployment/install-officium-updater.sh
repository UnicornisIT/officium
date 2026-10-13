#!/bin/sh

set -eu
umask 027

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root (sudo)." >&2
    exit 1
fi
if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    echo "Usage: $0 <service-user> [service-group]" >&2
    exit 2
fi

APP_USER="$1"
APP_GROUP="${2:-$1}"
for identity in "$APP_USER" "$APP_GROUP"; do
    printf '%s\n' "$identity" | grep -Eq '^[a-z_][a-z0-9_-]{0,31}$' || {
        echo "Invalid application user or group." >&2; exit 2;
    }
done
id "$APP_USER" >/dev/null 2>&1 || { echo "Unknown user: $APP_USER" >&2; exit 2; }
getent group "$APP_GROUP" >/dev/null 2>&1 || { echo "Unknown group: $APP_GROUP" >&2; exit 2; }

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
cd /
APP_ROOT="${APP_ROOT:-/opt/officium}"
STATE_DIR="${STATE_DIR:-/var/lib/officium}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/officium}"
UPDATE_SOCKET="${UPDATE_SOCKET:-/run/officium/updater.sock}"
case "$APP_ROOT:$STATE_DIR:$BACKUP_DIR:$UPDATE_SOCKET" in
    *".."*) echo "Parent path segments are not allowed." >&2; exit 2 ;;
esac
for path in "$APP_ROOT" "$STATE_DIR" "$BACKUP_DIR" "$UPDATE_SOCKET"; do
    case "$path" in /*) ;; *) echo "All paths must be absolute." >&2; exit 2 ;; esac
    printf '%s\n' "$path" | grep -Eq '^/[A-Za-z0-9_./@+-]+$' || {
        echo "A path contains unsupported characters." >&2; exit 2;
    }
done

install -o root -g root -m 0755 \
    "$SCRIPT_DIR/officium-update-runner" /usr/local/sbin/officium-update-runner
install -d -o root -g root -m 0755 /usr/local/lib/officium
install -o root -g root -m 0755 \
    "$SCRIPT_DIR/deploy-release" /usr/local/lib/officium/deploy-release
install -o root -g root -m 0755 \
    "$SCRIPT_DIR/officium-update-client" /usr/local/bin/officium-update-client

install -d -o root -g root -m 0750 /etc/officium
install -d -o root -g "$APP_GROUP" -m 0750 "$STATE_DIR"
install -d -o root -g root -m 0750 "$BACKUP_DIR"
if [ ! -e /etc/officium/updater.env ]; then
    sed \
        -e "s|<service-user>|$APP_USER|g" \
        -e "s|<service-group>|$APP_GROUP|g" \
        -e "s|^APP_DIR=.*|APP_DIR=$APP_ROOT/current|" \
        -e "s|^RELEASES_DIR=.*|RELEASES_DIR=$APP_ROOT/releases|" \
        -e "s|^BACKUP_DIR=.*|BACKUP_DIR=$BACKUP_DIR|" \
        -e "s|^STATUS_FILE=.*|STATUS_FILE=$STATE_DIR/server-update-status.json|" \
        "$SCRIPT_DIR/updater.env.example" >/etc/officium/updater.env
    chown root:root /etc/officium/updater.env
    chmod 0600 /etc/officium/updater.env
else
    echo "Preserved existing /etc/officium/updater.env."
fi

# Older installations did not have every key. Merge required safe defaults so
# the main installer can upgrade them without any manual file surgery.
ensure_setting() {
    setting_key="$1"
    setting_value="$2"
    if grep -q "^${setting_key}=" /etc/officium/updater.env; then
        sed -i "s|^${setting_key}=.*|${setting_key}=${setting_value}|" /etc/officium/updater.env
    else
        printf '%s=%s\n' "$setting_key" "$setting_value" >>/etc/officium/updater.env
    fi
}
ensure_setting APP_DIR "$APP_ROOT/current"
ensure_setting APP_USER "$APP_USER"
ensure_setting APP_GROUP "$APP_GROUP"
ensure_setting SERVICE_NAME "${SERVICE_NAME:-officium}"
ensure_setting UPDATE_SOCKET "$UPDATE_SOCKET"
ensure_setting SOURCE_URL "${SOURCE_URL:-https://github.com}"
ensure_setting GITHUB_API_URL "${GITHUB_API_URL:-https://api.github.com}"
ensure_setting STATUS_FILE "$STATE_DIR/server-update-status.json"
ensure_setting STALE_MINUTES 120
ensure_setting BACKUP_DIR "$BACKUP_DIR"
ensure_setting RELEASES_DIR "$APP_ROOT/releases"
ensure_setting DEPLOY_SCRIPT /usr/local/lib/officium/deploy-release
ensure_setting VENV_NAME venv
chown root:root /etc/officium/updater.env
chmod 0600 /etc/officium/updater.env

render_unit() {
    source_file="$1"
    destination="$2"
    sed \
        -e "s|@@APP_ROOT@@|$APP_ROOT|g" \
        -e "s|@@APP_GROUP@@|$APP_GROUP|g" \
        -e "s|@@UPDATE_SOCKET@@|$UPDATE_SOCKET|g" \
        -e "s|@@STATE_DIR@@|$STATE_DIR|g" \
        -e "s|@@BACKUP_DIR@@|$BACKUP_DIR|g" \
        "$source_file" >"$destination"
    chown root:root "$destination"
    chmod 0644 "$destination"
}
render_unit "$SCRIPT_DIR/officium-updater.socket.in" /etc/systemd/system/officium-updater.socket
render_unit "$SCRIPT_DIR/officium-updater.service.in" /etc/systemd/system/officium-updater.service

# Retire the obsolete sudo escalation rule without deleting its audit copy.
if [ -f /etc/sudoers.d/officium-updater ]; then
    mv /etc/sudoers.d/officium-updater /etc/sudoers.d/officium-updater.disabled
    chmod 0600 /etc/sudoers.d/officium-updater.disabled
fi

# systemd creates a missing socket parent directory as root:root 0750, which
# the web process cannot traverse (PermissionError on connect). tmpfiles.d
# creates it for the application group at boot, before sockets are started.
SOCKET_DIR=$(dirname "$UPDATE_SOCKET")
printf 'd %s 0750 root %s -\n' "$SOCKET_DIR" "$APP_GROUP" >/etc/tmpfiles.d/officium.conf
chown root:root /etc/tmpfiles.d/officium.conf
chmod 0644 /etc/tmpfiles.d/officium.conf
systemd-tmpfiles --create /etc/tmpfiles.d/officium.conf
chown root:"$APP_GROUP" "$SOCKET_DIR"
chmod 0750 "$SOCKET_DIR"

systemd-analyze verify \
    /etc/systemd/system/officium-updater.socket \
    /etc/systemd/system/officium-updater.service
systemctl daemon-reload
# Recreate the socket so it is bound inside the corrected directory.
systemctl stop officium-updater.service officium-updater.socket 2>/dev/null || true
systemctl enable --now officium-updater.socket

echo "Updater socket installed. Validate the actual web-process path with:"
echo "sudo -u $APP_USER /usr/local/bin/officium-update-client integration_check"
