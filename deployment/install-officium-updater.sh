#!/bin/sh

set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root (sudo)." >&2
    exit 1
fi

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <service-user>" >&2
    exit 2
fi
APP_USER="$1"
if ! printf '%s\n' "$APP_USER" | grep -Eq '^[a-z_][a-z0-9_-]{0,31}$'; then
    echo "Invalid application user." >&2
    exit 2
fi
if ! id "$APP_USER" >/dev/null 2>&1; then
    echo "Application user does not exist: $APP_USER" >&2
    exit 2
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SUDOERS_TEMP=$(mktemp /tmp/officium-updater-sudoers.XXXXXX)
trap 'rm -f "$SUDOERS_TEMP"' EXIT HUP INT TERM

install -o root -g root -m 0755 \
    "$SCRIPT_DIR/officium-update-runner" /usr/local/sbin/officium-update-runner
install -d -o root -g root -m 0750 /etc/officium
if [ ! -e /etc/officium/updater.env ]; then
    install -o root -g root -m 0600 \
        "$SCRIPT_DIR/updater.env.example" /etc/officium/updater.env
    echo "Created /etc/officium/updater.env; review it before enabling updates."
else
    echo "Preserved existing /etc/officium/updater.env."
fi

install -d -o root -g "$APP_USER" -m 2775 /var/lib/officium
install -d -o root -g root -m 0750 /var/backups/officium

sed "s/^<service-user> /$APP_USER /" "$SCRIPT_DIR/officium-updater.sudoers" > "$SUDOERS_TEMP"
chmod 0440 "$SUDOERS_TEMP"
visudo -cf "$SUDOERS_TEMP"
install -o root -g root -m 0440 "$SUDOERS_TEMP" /etc/sudoers.d/officium-updater
visudo -cf /etc/sudoers.d/officium-updater

echo "Updater installed. Run the readiness check as the application user:"
echo "sudo -u $APP_USER sudo -n /usr/local/sbin/officium-update-runner --check"
