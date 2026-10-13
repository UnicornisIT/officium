#!/usr/bin/env bats
# Unit tests for deployment/post-deploy-smoke.sh with stubbed system commands.

# Stubs that make every check except the one under test pass.
git() { echo 0123456789ab; }
curl() {
    local previous="" arg
    for arg in "$@"; do
        if [ "$previous" = --dump-header ]; then
            printf 'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n' >"$arg"
        fi
        previous="$arg"
    done
    return 0
}
systemctl() {
    case "$*" in
        *"-p InvocationID"*) echo 0123456789abcdef0123456789abcdef ;;
        *"-p NoNewPrivileges"*) echo yes ;;
        *"-p NRestarts"*) echo 0 ;;
        *) return 0 ;;
    esac
}
journalctl() { printf '%s\n' "$@" >"$JOURNAL_ARGS"; }

setup() {
    ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
    # shellcheck source=../../deployment/post-deploy-smoke.sh
    source "$ROOT/deployment/post-deploy-smoke.sh"
    TMP="$(mktemp -d)"
    APP="$TMP/app"
    mkdir -p "$APP/venv/bin"
    printf 'v9.9.9\n' >"$APP/VERSION"
    printf '#!/bin/sh\nexit 0\n' >"$APP/venv/bin/python"
    chmod +x "$APP/venv/bin/python"
    JOURNAL_ARGS="$TMP/journal-args"
    export JOURNAL_ARGS
    export -f git curl systemctl journalctl
    SMOKE="$ROOT/deployment/post-deploy-smoke.sh"
}

teardown() {
    rm -rf "$TMP"
}

@test "nginx host: finds the server_name that proxies to the app port" {
    run nginx_public_host_from_config 5000 <<'NGINX'
# configuration file /etc/nginx/sites-enabled/default:
server {
    listen 80 default_server;
    server_name _;
    root /var/www/html;
}
# configuration file /etc/nginx/sites-enabled/officium:
server {
    listen 80;
    server_name finance.example.org www.finance.example.org;
    # location /static/ { alias /opt/officium/static/; }
    location / {
        proxy_pass http://127.0.0.1:5000;
    }
}
NGINX
    [ "$status" -eq 0 ]
    [ "$output" = "finance.example.org" ]
}

@test "nginx host: another port is ignored and a catch-all becomes localhost" {
    run nginx_public_host_from_config 5000 <<'NGINX'
server {
    server_name other.example.org;
    location / { proxy_pass http://127.0.0.1:8080; }
}
server {
    server_name _;
    location / { proxy_pass http://localhost:5000/; }
}
NGINX
    [ "$output" = "localhost" ]
    run nginx_public_host_from_config 6000 <<'NGINX'
server { server_name a.example.org; location / { proxy_pass http://127.0.0.1:5000; } }
NGINX
    [ -z "$output" ]
}

@test "journal errors are counted for the current invocation, not a time window" {
    run critical_errors_since_start
    [ "$status" -eq 0 ]
    [ "$output" = 0 ]
    grep -qx '_SYSTEMD_INVOCATION_ID=0123456789abcdef0123456789abcdef' "$JOURNAL_ARGS"
    grep -qx 'INVOCATION_ID=0123456789abcdef0123456789abcdef' "$JOURNAL_ARGS"
    run grep -q -- '--since' "$JOURNAL_ARGS"
    [ "$status" -ne 0 ]
}

@test "a crash loop before the repair does not count against the new run" {
    journalctl() {
        # Only entries of an older invocation exist.
        case "$*" in *"_SYSTEMD_INVOCATION_ID=0123456789abcdef0123456789abcdef"*) return 0 ;; esac
        printf 'old crash\nold crash\n'
    }
    run critical_errors_since_start
    [ "$output" = 0 ]
}

@test "a passing run prints every check and SMOKE PASS" {
    run bash "$SMOKE" --app-dir "$APP" --public-host ''
    [ "$status" -eq 0 ]
    [[ "$output" == *"PASS service active"* ]]
    [[ "$output" == *"PASS journal errors since service start"* ]]
    [[ "$output" == *"SMOKE PASS version=v9.9.9 commit=0123456789ab"* ]]
}

@test "a failing check is named instead of exiting silently" {
    systemctl() {
        case "$*" in is-active*officium.service*) return 3 ;; *) return 0 ;; esac
    }
    export -f systemctl
    run bash "$SMOKE" --app-dir "$APP" --public-host ''
    [ "$status" -eq 1 ]
    [[ "$output" == *"FAIL service active: officium.service is not active"* ]]
}

@test "an unexpected command failure names the check and the command" {
    rm "$APP/VERSION"
    run bash "$SMOKE" --app-dir "$APP" --public-host ''
    [ "$status" -eq 1 ]
    [[ "$output" == *"FAIL version: command"* ]]
}

@test "critical entries of the current run fail the smoke test with a count" {
    journalctl() { printf 'Traceback\nError\n'; }
    run bash "$SMOKE" --app-dir "$APP" --public-host ''
    [ "$status" -eq 1 ]
    [[ "$output" == *"FAIL journal errors since service start: 2 critical entries"* ]]
}

@test "non-loopback smoke URLs are refused" {
    run bash "$SMOKE" --app-dir "$APP" --url http://example.org:5000/login
    [ "$status" -eq 1 ]
    [[ "$output" == *"FAIL arguments: smoke URL must be loopback HTTP"* ]]
}
