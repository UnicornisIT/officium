#!/usr/bin/env bats

setup() {
    ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
    mkdir -p "$ROOT/.tmp"
    TMP="$(mktemp -d "$ROOT/.tmp/install-env.XXXXXX")"
    APP_DIR="$TMP/app"
    BACKUP_DIR="$TMP/backups"
    STATE_DIR="$TMP/state"
    UPDATE_SOCKET="$TMP/updater.sock"
    APP_USER=officium
    APP_GROUP=officium
    MODE=install
    ENV_FILE_SOURCE="$TMP/prepared.env"
    mkdir -p "$APP_DIR/shared" "$BACKUP_DIR"
    printf '# HTTPS_PROXY=http://default.example:3128\n' >"$ENV_FILE_SOURCE"
}

teardown() {
    local target scratch_root
    target=$(realpath -e "$TMP")
    scratch_root=$(realpath -e "$ROOT/.tmp")
    case "$target" in
        "$scratch_root"/install-env.*) rm -rf -- "$target" ;;
        *) return 1 ;;
    esac
}

configure_environment_only() {
    # Проверяем штатные функции, не выполняя установку systemd или root-команды.
    eval "$(sed -n '/^set_env_value() {$/,/^# The only directories/p' "$ROOT/deployment/install-officium.sh" | sed '$d')"
    install() { cp -- "${@: -2:1}" "${@: -1}"; }
    chown() { :; }
    chmod() { :; }
    ensure_runtime_dirs() { :; }
    info() { :; }
    fail() { return 1; }
    eval "$(sed -n '/^if \[ "\$MODE" = install \]; then$/,/^info "Installing restricted updater"/p' "$ROOT/deployment/install-officium.sh" | sed '$d')"
}

@test "retrying first install preserves enabled proxy and existing environment" {
    printf 'HTTPS_PROXY=http://existing:encoded-pass@proxy.example:3128\nNO_PROXY=localhost,custom.internal\n' >"$APP_DIR/shared/.env"
    configure_environment_only
    grep -qx 'HTTPS_PROXY=http://existing:encoded-pass@proxy.example:3128' "$APP_DIR/shared/.env"
    grep -qx 'NO_PROXY=localhost,custom.internal' "$APP_DIR/shared/.env"
    ! grep -q 'default.example' "$APP_DIR/shared/.env"
    [ "$(find "$BACKUP_DIR" -type f | wc -l)" -eq 1 ]
}

@test "fresh install keeps optional proxy commented" {
    configure_environment_only
    grep -qx '# HTTPS_PROXY=http://default.example:3128' "$APP_DIR/shared/.env"
    ! grep -q '^HTTPS_PROXY=' "$APP_DIR/shared/.env"
}
