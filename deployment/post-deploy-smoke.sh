#!/bin/bash
# Read-only post-deploy verification. It never creates users or financial rows.

set -euo pipefail

SERVICE_NAME="officium"
APP_DIR="/opt/officium/current"
LOCAL_URL="http://127.0.0.1:5000/login"
TIMER_NAME="officium-monthly-expenses.timer"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --service) SERVICE_NAME="${2:-}"; shift 2 ;;
        --app-dir) APP_DIR="${2:-}"; shift 2 ;;
        --url) LOCAL_URL="${2:-}"; shift 2 ;;
        --timer) TIMER_NAME="${2:-}"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

[[ "$SERVICE_NAME" =~ ^[A-Za-z0-9_][A-Za-z0-9_.@-]{0,127}$ ]] || exit 2
[[ "$TIMER_NAME" =~ ^[A-Za-z0-9_][A-Za-z0-9_.@-]{0,127}$ ]] || exit 2
[[ "$APP_DIR" = /* && "$APP_DIR" != *".."* ]] || exit 2
case "$LOCAL_URL" in http://127.0.0.1:*|http://localhost:*|http://\[::1\]:*) ;; *) echo "Smoke URL must be loopback HTTP." >&2; exit 2 ;; esac

venv_python=""
for candidate in "$APP_DIR/venv/bin/python" "$APP_DIR/.venv/bin/python"; do
    [ -x "$candidate" ] && venv_python="$candidate" && break
done
[ -n "$venv_python" ] || { echo "FAIL: virtualenv Python is missing" >&2; exit 1; }

version=$(tr -d '\r\n' <"$APP_DIR/VERSION")
commit=$(git -C "$APP_DIR" rev-parse --short=12 HEAD)
printf 'version=%s commit=%s\n' "$version" "$commit"

systemctl is-active --quiet "$SERVICE_NAME.service"
systemctl is-active --quiet "$TIMER_NAME"
systemctl show "$SERVICE_NAME.service" -p NoNewPrivileges --value | grep -qx yes

(
    cd "$APP_DIR"
    FLASK_APP=run.py "$venv_python" -c 'from run import app; assert app is not None; print("import=PASS")'
    # These commands query migration metadata only; they do not modify schema.
    FLASK_APP=run.py "$venv_python" -m flask db current >/dev/null
    FLASK_APP=run.py "$venv_python" -m flask db heads >/dev/null
)

headers=$(mktemp)
body=$(mktemp)
trap 'rm -f "$headers" "$body"' EXIT
curl --silent --show-error --fail --max-time 10 --dump-header "$headers" --output "$body" "$LOCAL_URL"
grep -qi '^Content-Type: text/html' "$headers"
static_url="${LOCAL_URL%/login}/static/favicon.svg"
curl --silent --show-error --fail --max-time 10 --output /dev/null "$static_url"

# Report only the count, not journal contents that might contain user data.
critical_count=$(journalctl -u "$SERVICE_NAME.service" --since '-10 minutes' -p 0..3 --no-pager --output=cat 2>/dev/null | wc -l)
printf 'http=PASS static=PASS db_migrations=PASS timer=PASS critical_errors_last_10m=%s\n' "$critical_count"
[ "$critical_count" -eq 0 ] || { echo "FAIL: critical service errors detected" >&2; exit 1; }
