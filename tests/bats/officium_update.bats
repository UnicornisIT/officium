#!/usr/bin/env bats
# Unit tests for helpers in scripts/officium-update.

setup() {
    ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
    # shellcheck source=../../scripts/officium-update
    source "$ROOT/scripts/officium-update"
    TMP="$(mktemp -d)"
    # Windows developer machines have a Store stub named python3.
    if ! python3 -c 'pass' >/dev/null 2>&1; then
        python3() { python "$@"; }
    fi
}

teardown() {
    rm -rf "$TMP"
}

# `systemctl show -p ExecStart --value` of the legacy v0.0.7 unit and of the
# manual work-around drop-in seen on the production server.
LEGACY_EXEC='{ path=/opt/officium/venv/bin/waitress-serve ; argv[]=/opt/officium/venv/bin/waitress-serve --listen=127.0.0.1:5000 --threads=8 --trusted-proxy=127.0.0.1 --trusted-proxy-headers=x-forwarded-for x-forwarded-host x-forwarded-proto x-forwarded-port run:app ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }'
DROPIN_EXEC='{ path=/opt/officium/current/venv/bin/python ; argv[]=/opt/officium/current/venv/bin/python -m waitress --listen=[::1]:5050 --threads=4 --trusted-proxy=::1 run:app ; ignore_errors=no }'

@test "env values are read without sourcing and the last assignment wins" {
    cat >"$TMP/.env" <<'ENV'
# comment
DB_NAME=first
export APP_TIMEZONE="Europe/Moscow"
DB_NAME='officium'
DB_PASSWORD=p@ss word;$(touch /tmp/pwned)
ENV
    [ "$(read_env_value "$TMP/.env" DB_NAME)" = officium ]
    [ "$(read_env_value "$TMP/.env" APP_TIMEZONE)" = Europe/Moscow ]
    [ "$(read_env_value "$TMP/.env" MISSING)" = "" ]
    [ "$(read_env_value "$TMP/missing" DB_NAME)" = "" ]
    [ ! -e /tmp/pwned ]
}

@test "waitress options come from the legacy console-script unit" {
    run parse_exec_start "$LEGACY_EXEC"
    [ "$status" -eq 0 ]
    [[ "$output" == *"HOST=127.0.0.1"* ]]
    [[ "$output" == *"PORT=5000"* ]]
    [[ "$output" == *"WAITRESS_THREADS=8"* ]]
    [[ "$output" == *"TRUSTED_PROXY=127.0.0.1"* ]]
    [[ "$output" == *"TRUSTED_PROXY_HEADERS=x-forwarded-for x-forwarded-host x-forwarded-proto x-forwarded-port"* ]]
    [[ "$output" == *"UNIT_VENV=/opt/officium/venv"* ]]
}

@test "waitress options come from a python -m waitress drop-in with IPv6" {
    run parse_exec_start "$DROPIN_EXEC"
    [[ "$output" == *"HOST=::1"* ]]
    [[ "$output" == *"PORT=5050"* ]]
    [[ "$output" == *"TRUSTED_PROXY=::1"* ]]
    [[ "$output" == *"UNIT_VENV=/opt/officium/current/venv"* ]]
    [[ "$output" != *"TRUSTED_PROXY_HEADERS"* ]]
}

@test "the repository comes from a GitHub remote of the legacy checkout" {
    [ "$(github_repository_from_remote https://github.com/UnicornisIT/officium.git)" = UnicornisIT/officium ]
    [ "$(github_repository_from_remote git@github.com:UnicornisIT/officium.git)" = UnicornisIT/officium ]
    [ "$(github_repository_from_remote https://gitlab.com/x/officium.git)" = "" ]
}

@test "only a dump with the mysqldump trailer is accepted" {
    printf -- '-- MySQL dump\nINSERT INTO x VALUES (1);\n-- Dump completed on 2026-10-14 10:00:00\n' >"$TMP/ok.sql"
    printf -- '-- MySQL dump\nINSERT INTO x VALUES (1);\n' >"$TMP/cut.sql"
    : >"$TMP/empty.sql"
    dump_is_complete "$TMP/ok.sql"
    run dump_is_complete "$TMP/cut.sql"
    [ "$status" -ne 0 ]
    run dump_is_complete "$TMP/empty.sql"
    [ "$status" -ne 0 ]
}

@test "rollback picks the newest completed release built before the current one" {
    for name in v1-a v2-b v3-c v4-broken; do mkdir -p "$TMP/releases/$name"; done
    mkdir -p "$TMP/releases/.build-x/v5-d"
    touch -d '2026-10-01' "$TMP/releases/v1-a/.officium-release-ready"
    touch -d '2026-10-05' "$TMP/releases/v2-b/.officium-release-ready"
    touch -d '2026-10-09' "$TMP/releases/v3-c/.officium-release-ready"
    # A newer release whose update was rolled back is never the target.
    touch -d '2026-10-12' "$TMP/releases/v4-broken/.officium-release-ready"
    run previous_release "$TMP/releases" "$TMP/releases/v3-c"
    [ "$status" -eq 0 ]
    [ "$output" = "$TMP/releases/v2-b" ]
    run previous_release "$TMP/releases" "$TMP/releases/v1-a"
    [ "$status" -ne 0 ]
    rm "$TMP/releases/v1-a/.officium-release-ready" "$TMP/releases/v2-b/.officium-release-ready"
    run previous_release "$TMP/releases" "$TMP/releases/v3-c"
    [ "$status" -ne 0 ]
}

@test "arguments: commands, tag, flags and refusals" {
    parse_args --yes --tag v1.2.3
    [ "$COMMAND" = tag ] && [ "$TARGET_TAG" = v1.2.3 ] && [ "$ASSUME_YES" = true ]
    COMMAND="" ROLLBACK_TO=""
    parse_args rollback --to /opt/officium/releases/v1-a --dry-run
    [ "$COMMAND" = rollback ] && [ "$ROLLBACK_TO" = /opt/officium/releases/v1-a ] && [ "$DRY_RUN" = true ]
    run bash "$ROOT/scripts/officium-update" --tag 'v1;rm -rf /'
    [ "$status" -eq 2 ]
    run bash "$ROOT/scripts/officium-update" latest --to /x
    [ "$status" -eq 2 ]
    run bash "$ROOT/scripts/officium-update"
    [ "$status" -eq 2 ]
}

@test "missing parameters are listed by name before anything changes" {
    LAYOUT=legacy APP_ROOT=/opt/officium ENV_FILE="$TMP/none" APP_USER="" APP_GROUP=officium
    PORT="" APP_TIMEZONE="" REPOSITORY="" SERVICE_NAME=officium UPDATER_ENV=/etc/officium/updater.env
    run require_parameters
    [ "$status" -eq 2 ]
    [[ "$output" == *"readable application .env"* ]]
    [[ "$output" == *"User= of officium.service"* ]]
    [[ "$output" == *"--listen=HOST:PORT"* ]]
    [[ "$output" == *"APP_TIMEZONE in"* ]]
    [[ "$output" == *"OFFICIUM_REPOSITORY=owner/name"* ]]
    [[ "$output" != *"Group="* ]]
}

@test "the installer gets the discovered parameters, never a password" {
    APP_ROOT=/opt/officium APP_USER=officium APP_GROUP=officium SERVICE_NAME=officium
    MONTHLY_SERVICE=officium-monthly-expenses HOST=127.0.0.1 PORT=5000 WAITRESS_THREADS=8
    TRUSTED_PROXY=127.0.0.1 TRUSTED_PROXY_HEADERS="x-forwarded-for x-forwarded-proto" APP_TIMEZONE=Europe/Moscow
    DB_ENGINE=mysql DB_NAME=officium BACKUP_DIR=/var/backups/officium STATE_DIR=/var/lib/officium
    UPDATE_SOCKET=/run/officium/updater.sock REPOSITORY=UnicornisIT/officium
    SOURCE_URL=https://github.com GITHUB_API_URL=https://api.github.com
    run installer_environment
    [[ "$output" == *"VENV_DIR=/opt/officium/current/venv"* ]]
    [[ "$output" == *"WAITRESS_THREADS=8"* ]]
    [[ "$output" == *"TRUSTED_PROXY_HEADERS=x-forwarded-for x-forwarded-proto"* ]]
    [[ "$output" != *"PASSWORD"* ]]
}

@test "waiting on the runner: success, rollback and a crashed unit" {
    STATUS_FILE="$TMP/status.json"
    systemctl() { echo active; }
    sleep() { :; }
    printf '{"state":"success","message":"done"}' >"$STATUS_FILE"
    run wait_for_update v2 officium-update-x.service
    [ "$status" -eq 0 ]
    printf '{"state":"failed","error_code":"healthcheck_failed","rollback_performed":true}' >"$STATUS_FILE"
    run wait_for_update v2 officium-update-x.service
    [ "$status" -eq 1 ]
    printf '{"state":"failed","error_code":"rollback_failed","rollback_performed":false}' >"$STATUS_FILE"
    run wait_for_update v2 officium-update-x.service
    [ "$status" -eq 3 ]  # failed without rollback: the caller checks current/
    printf '{"state":"installing"}' >"$STATUS_FILE"
    systemctl() { echo failed; }
    run wait_for_update v2 officium-update-x.service
    [ "$status" -eq 2 ]
    [[ "$output" == *"stopped without a final status"* ]]
}
