#!/bin/bash
# Read-only post-deploy verification. It never creates users or financial rows.
#
# Every check has a name; whatever fails, the script prints which check failed
# and why instead of exiting silently under set -e.

SERVICE_NAME="officium"
APP_DIR="/opt/officium/current"
LOCAL_URL="http://127.0.0.1:5000/login"
TIMER_NAME="officium-monthly-expenses.timer"
# Host header of the public nginx vhost: "auto" finds the server_name that
# proxies to LOCAL_URL's port, "" skips the nginx static check.
PUBLIC_HOST="auto"
CHECK="startup"

fail_check() {
    printf 'FAIL %s: %s\n' "$CHECK" "$*" >&2
    exit 1
}

on_error() {
    local status=$? line="$1" command="$2"
    printf 'FAIL %s: command "%s" exited with %s (line %s)\n' "$CHECK" "$command" "$status" "$line" >&2
    exit 1
}

pass() {
    printf 'PASS %s%s\n' "$CHECK" "${1:+ ($1)}"
}

# Reads `nginx -T` output on stdin and prints the server_name of the first
# server block that proxies to 127.0.0.1/localhost/[::1]:PORT.
nginx_public_host_from_config() {
    local port="$1"
    awk -v port="$port" '
        /^[[:space:]]*#/ { next }
        /^[[:space:]]*server[[:space:]]*\{/ { name = "" }
        /^[[:space:]]*server_name[[:space:]]/ {
            line = $0
            sub(/^[[:space:]]*server_name[[:space:]]+/, "", line)
            sub(/[[:space:];].*$/, "", line)
            if (name == "") name = line
        }
        /proxy_pass[[:space:]]+https?:\/\/(127\.0\.0\.1|localhost|\[::1\]):[0-9]+/ {
            target = $0
            sub(/.*:\/\/(127\.0\.0\.1|localhost|\[::1\]):/, "", target)
            sub(/[^0-9].*$/, "", target)
            if (target == port && name != "") { print (name == "_" ? "localhost" : name); exit }
        }
    '
}

# Critical journal entries of the *current* service run only. A fixed time
# window still counted the crash loop that preceded a repair for ten minutes.
critical_errors_since_start() {
    local invocation
    invocation=$(systemctl show "$SERVICE_NAME.service" -p InvocationID --value)
    [[ "$invocation" =~ ^[0-9a-f]{32}$ ]] || { echo "unknown"; return 0; }
    journalctl --no-pager --output=cat -p 0..3 \
        "_SYSTEMD_INVOCATION_ID=$invocation" + "INVOCATION_ID=$invocation" 2>/dev/null | wc -l
}

check_static_through_nginx() {
    local host="$1" port="$2" headers status location
    headers=$(mktemp)
    status=$(curl --silent --show-error --max-time 10 --output /dev/null --dump-header "$headers" \
        --write-out '%{http_code}' --header "Host: $host" "http://127.0.0.1/static/css/style.css") || \
        fail_check "nginx on 127.0.0.1:80 is not answering for Host: $host"
    if [[ "$status" =~ ^30[178]$ ]]; then
        location=$(sed -n 's/^[Ll]ocation:[[:space:]]*//p' "$headers" | tr -d '\r')
        [[ "$location" = https://* ]] || fail_check "Host $host redirects /static/ to $location"
        # Local check of the TLS vhost; the certificate is checked by browsers.
        status=$(curl --silent --show-error --max-time 10 --insecure --output /dev/null \
            --dump-header "$headers" --write-out '%{http_code}' \
            --resolve "$host:443:127.0.0.1" "https://$host/static/css/style.css") || \
            fail_check "nginx HTTPS vhost for $host is not answering"
    fi
    [ "$status" = 200 ] || { rm -f "$headers"; fail_check "/static/css/style.css via Host $host answered HTTP $status"; }
    grep -qi '^Content-Type: text/css' "$headers" || {
        rm -f "$headers"; fail_check "/static/css/style.css via Host $host is not text/css"; }
    rm -f "$headers"
    pass "Host: $host, port $port"
}

main() {
    set -Eeuo pipefail
    trap 'on_error "$LINENO" "$BASH_COMMAND"' ERR

    while [ "$#" -gt 0 ]; do
        case "$1" in
            --service) SERVICE_NAME="${2:-}"; shift 2 ;;
            --app-dir) APP_DIR="${2:-}"; shift 2 ;;
            --url) LOCAL_URL="${2:-}"; shift 2 ;;
            --timer) TIMER_NAME="${2:-}"; shift 2 ;;
            --public-host) PUBLIC_HOST="${2:-}"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 2 ;;
        esac
    done

    CHECK="arguments"
    [[ "$SERVICE_NAME" =~ ^[A-Za-z0-9_][A-Za-z0-9_.@-]{0,127}$ ]] || fail_check "unsafe service name"
    [[ "$TIMER_NAME" =~ ^[A-Za-z0-9_][A-Za-z0-9_.@-]{0,127}$ ]] || fail_check "unsafe timer name"
    [[ "$APP_DIR" = /* && "$APP_DIR" != *".."* ]] || fail_check "APP_DIR must be absolute"
    [[ -z "$PUBLIC_HOST" || "$PUBLIC_HOST" = auto || "$PUBLIC_HOST" =~ ^[A-Za-z0-9.-]+$ ]] || \
        fail_check "unsafe public host"
    case "$LOCAL_URL" in
        http://127.0.0.1:*|http://localhost:*|http://\[::1\]:*) ;;
        *) fail_check "smoke URL must be loopback HTTP" ;;
    esac
    local port
    [[ "$LOCAL_URL" =~ :([0-9]+)(/|$) ]] || fail_check "smoke URL needs an explicit port"
    port="${BASH_REMATCH[1]}"

    CHECK="virtualenv"
    local venv_python=""
    for candidate in "$APP_DIR/venv/bin/python" "$APP_DIR/.venv/bin/python"; do
        [ -x "$candidate" ] && venv_python="$candidate" && break
    done
    [ -n "$venv_python" ] || fail_check "virtualenv Python is missing in $APP_DIR"
    pass

    CHECK="version"
    local version commit
    version=$(tr -d '\r\n' <"$APP_DIR/VERSION")
    commit=$(git -C "$APP_DIR" -c "safe.directory=$(realpath -e "$APP_DIR")" rev-parse --short=12 HEAD)
    pass "version=$version commit=$commit"

    CHECK="environment link"
    if [ -L "$APP_DIR" ]; then
        local shared_env
        shared_env="$(dirname "$APP_DIR")/shared/.env"
        [ "$(realpath -e "$APP_DIR/.env" 2>/dev/null)" = "$(realpath -e "$shared_env")" ] || \
            fail_check "$APP_DIR/.env does not resolve to $shared_env"
    fi
    pass

    CHECK="service active"
    systemctl is-active --quiet "$SERVICE_NAME.service" || fail_check "$SERVICE_NAME.service is not active"
    pass "NRestarts=$(systemctl show "$SERVICE_NAME.service" -p NRestarts --value)"

    CHECK="timer active"
    systemctl is-active --quiet "$TIMER_NAME" || fail_check "$TIMER_NAME is not active"
    pass

    CHECK="NoNewPrivileges"
    systemctl show "$SERVICE_NAME.service" -p NoNewPrivileges --value | grep -qx yes || \
        fail_check "$SERVICE_NAME.service runs without NoNewPrivileges=yes"
    pass

    CHECK="application import"
    (
        cd "$APP_DIR"
        FLASK_APP=run.py "$venv_python" -c 'from run import app; assert app is not None' >/dev/null
    ) || fail_check "run:app does not import with $APP_DIR/.env"
    pass

    CHECK="migration state"
    (
        cd "$APP_DIR"
        # These commands query migration metadata only; they do not modify schema.
        FLASK_APP=run.py "$venv_python" -m flask db current >/dev/null
        FLASK_APP=run.py "$venv_python" -m flask db heads >/dev/null
    ) || fail_check "flask db current/heads failed"
    pass

    CHECK="login page"
    # Global, not local: the EXIT trap runs after main has returned.
    SMOKE_HEADERS=$(mktemp)
    SMOKE_BODY=$(mktemp)
    trap 'rm -f "$SMOKE_HEADERS" "$SMOKE_BODY"' EXIT
    curl --silent --show-error --fail --max-time 10 --dump-header "$SMOKE_HEADERS" \
        --output "$SMOKE_BODY" "$LOCAL_URL" || fail_check "$LOCAL_URL did not answer 2xx"
    grep -qi '^Content-Type: text/html' "$SMOKE_HEADERS" || fail_check "$LOCAL_URL is not text/html"
    pass

    CHECK="static from the application"
    curl --silent --show-error --fail --max-time 10 --output /dev/null \
        "${LOCAL_URL%/login}/static/css/style.css" || fail_check "application does not serve /static/css/style.css"
    pass

    CHECK="static through nginx"
    if [ -z "$PUBLIC_HOST" ]; then
        pass "skipped by --public-host ''"
    elif ! command -v nginx >/dev/null 2>&1; then
        pass "skipped: nginx is not installed"
    else
        local host="$PUBLIC_HOST"
        if [ "$host" = auto ]; then
            host=$(nginx -T 2>/dev/null | nginx_public_host_from_config "$port" || true)
        fi
        if [ -z "$host" ]; then
            pass "skipped: no nginx server block proxies to port $port"
        else
            check_static_through_nginx "$host" "$port"
        fi
    fi

    CHECK="journal errors since service start"
    local critical_count
    # Report only the count, not journal contents that might contain user data.
    critical_count=$(critical_errors_since_start)
    [ "$critical_count" = 0 ] || fail_check "$critical_count critical entries in the current service run"
    pass

    printf 'SMOKE PASS version=%s commit=%s\n' "$version" "$commit"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
