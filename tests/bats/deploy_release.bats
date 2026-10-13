#!/usr/bin/env bats
# Unit tests for helpers in deployment/deploy-release.

setup() {
    ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
    # shellcheck source=../../deployment/deploy-release
    source "$ROOT/deployment/deploy-release"
    TMP="$(mktemp -d)"
    RELEASE="$TMP/releases/.build-v1.x/v1-0123456789ab"
    mkdir -p "$RELEASE/venv/bin" "$RELEASE/.venv/bin"
}

teardown() {
    rm -rf "$TMP"
}

@test "venv scripts that point inside the release pass" {
    printf '#!%s/venv/bin/python\nimport sys\n' "$RELEASE" >"$RELEASE/venv/bin/flask"
    printf "VIRTUAL_ENV='%s/venv'\n" "$RELEASE" >"$RELEASE/venv/bin/activate"
    printf 'set -gx VIRTUAL_ENV "%s/venv"\n' "$RELEASE" >"$RELEASE/venv/bin/activate.fish"
    run verify_venv_paths "$RELEASE" venv
    [ "$status" -eq 0 ]
}

@test "a console script still pointing at the staging checkout fails the build" {
    printf '#!%s/releases/.build-v1.x/.staging-v1.abc/source/venv/bin/python\n' "$TMP" \
        >"$RELEASE/venv/bin/waitress-serve"
    run verify_venv_paths "$RELEASE" venv
    [ "$status" -eq 1 ]
    [[ "$output" == *"waitress-serve references"*".staging-v1.abc/source"* ]]
}

@test "an activate script of a moved release fails the build" {
    printf "VIRTUAL_ENV='/opt/officium.managed.42/releases/v1-0123456789ab/venv'\n" \
        >"$RELEASE/venv/bin/activate"
    run verify_venv_paths "$RELEASE" venv
    [ "$status" -eq 1 ]
    [[ "$output" == *"/opt/officium.managed.42/"* ]]
}

@test "a sibling release with a longer name is not inside the release" {
    printf '#!%s-old/venv/bin/python\n' "$RELEASE" >"$RELEASE/venv/bin/flask"
    run verify_venv_paths "$RELEASE" venv
    [ "$status" -eq 1 ]
}

@test "the .venv layout is checked the same way" {
    printf '#!%s/.venv/bin/python\n' "$RELEASE" >"$RELEASE/.venv/bin/flask"
    run verify_venv_paths "$RELEASE" .venv
    [ "$status" -eq 0 ]
    printf '#!/elsewhere/.venv/bin/python\n' >"$RELEASE/.venv/bin/pip"
    run verify_venv_paths "$RELEASE" .venv
    [ "$status" -eq 1 ]
}

@test "a broken interpreter link fails the build" {
    ln -s "$TMP/missing-python" "$RELEASE/venv/bin/python" || skip "symlinks are not available"
    [ -L "$RELEASE/venv/bin/python" ] || skip "symlinks are emulated on this platform"
    run verify_venv_paths "$RELEASE" venv
    [ "$status" -eq 1 ]
    [[ "$output" == *"link is broken"* ]]
}

@test "an explicit .env link to the permanent shared file is kept" {
    run environment_link_target "/opt/officium.managed.42/shared/.env" "/opt/officium/shared/.env"
    [ "$status" -eq 0 ]
    [ "$output" = "/opt/officium/shared/.env" ]
}

@test "a .env link through current/ is refused" {
    run environment_link_target "$TMP/x" "/opt/officium/current/.env"
    [ "$status" -eq 1 ]
    [[ "$output" == *"current/"* ]]
}

@test "a .env link into the temporary migration tree is refused" {
    run environment_link_target "$TMP/x" "/opt/officium.managed.42/shared/.env"
    [ "$status" -eq 1 ]
}

@test "a .env link that is not <APP_ROOT>/shared/.env is refused" {
    run environment_link_target "$TMP/x" "/opt/officium/.env"
    [ "$status" -eq 1 ]
    run environment_link_target "$TMP/x" "shared/.env"
    [ "$status" -eq 1 ]
}

@test "without an explicit link the build file is resolved to its real path" {
    mkdir -p "$TMP/app/shared" "$TMP/app/releases/v1"
    printf 'SECRET_KEY=x\n' >"$TMP/app/shared/.env"
    ln -s "$TMP/app/shared/.env" "$TMP/app/releases/v1/.env" || skip "symlinks are not available"
    [ -L "$TMP/app/releases/v1/.env" ] || skip "symlinks are emulated on this platform"
    ln -s "$TMP/app/releases/v1" "$TMP/app/current"
    run environment_link_target "$TMP/app/current/.env" ""
    [ "$status" -eq 0 ]
    [ "$output" = "$(realpath -e "$TMP/app/shared/.env")" ]
}
