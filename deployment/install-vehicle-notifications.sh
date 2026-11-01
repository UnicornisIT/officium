#!/bin/bash
# Run after the main installer; variables must match the application .env.
set -euo pipefail
APP_ROOT=${APP_ROOT:-/opt/officium}
APP_USER=${APP_USER:-officium}
APP_GROUP=${APP_GROUP:-officium}
VENV_DIR=${VENV_DIR:-$APP_ROOT/current/venv}
APP_TIMEZONE=${APP_TIMEZONE:-Europe/Moscow}
VEHICLE_NOTIFICATIONS_HOUR=${VEHICLE_NOTIFICATIONS_HOUR:-10}
VEHICLE_NOTIFICATIONS_ENABLED=${VEHICLE_NOTIFICATIONS_ENABLED:-false}
[[ "$VEHICLE_NOTIFICATIONS_HOUR" =~ ^([0-9]|1[0-9]|2[0-3])$ ]] || { echo 'Invalid hour' >&2; exit 1; }
[[ "$APP_TIMEZONE" =~ ^[A-Za-z_+-]+(/[A-Za-z0-9_+-]+)+$|^UTC$ ]] || exit 1
unit=officium-vehicle-notifications
if [[ "$VEHICLE_NOTIFICATIONS_ENABLED" != true ]]; then
    systemctl disable --now "$unit.timer" 2>/dev/null || true
    exit 0
fi
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
for extension in service timer; do
    sed -e "s|@@APP_ROOT@@|$APP_ROOT|g" -e "s|@@APP_USER@@|$APP_USER|g" \
        -e "s|@@APP_GROUP@@|$APP_GROUP|g" -e "s|@@VENV_DIR@@|$VENV_DIR|g" \
        -e "s|@@APP_TIMEZONE@@|$APP_TIMEZONE|g" \
        -e "s|@@VEHICLE_NOTIFICATIONS_HOUR@@|$VEHICLE_NOTIFICATIONS_HOUR|g" \
        "$script_dir/$unit.$extension" >"/etc/systemd/system/$unit.$extension"
    chmod 0644 "/etc/systemd/system/$unit.$extension"
done
systemd-analyze verify "/etc/systemd/system/$unit.service" "/etc/systemd/system/$unit.timer"
systemctl daemon-reload
systemctl enable --now "$unit.timer"
