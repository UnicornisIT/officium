#!/bin/bash
# Managed-install scenarios on a disposable Ubuntu machine (run as root):
#   1. clean install of v90.0.1 with nginx
#   3. two runner updates in a row, v90.0.1 -> v90.0.2 -> v90.0.3
#   4. the admin-button path: integration_check and start over IPC as APP_USER
#   5. failure injection: v90.0.5 cannot start -> automatic rollback
# with assert-deployment.sh (scenario 6) after each step.

set -euo pipefail

workspace="${1:?usage: scenario-managed.sh WORKSPACE ENV_FILE}"
env_file="${2:?usage: scenario-managed.sh WORKSPACE ENV_FILE}"
here="$workspace/tests/integration/deploy"
publish() { printf '%s\n' "$1" >/srv/qa-api/latest; }
scenario() { printf '\n========== %s ==========\n' "$*"; }

export REPOSITORY=qa/officium SOURCE_URL=file:///srv/qa-git GITHUB_API_URL=http://127.0.0.1:8765

scenario "1. clean install of v90.0.1"
publish v90.0.1
env RELEASE_TAG=v90.0.1 ENV_FILE_SOURCE="$env_file" APP_DIR=/opt/officium APP_USER=officium \
    APP_GROUP=officium SERVICE_NAME=officium HOST=127.0.0.1 PORT=5000 TRUSTED_PROXY=127.0.0.1 \
    APP_TIMEZONE=Europe/Moscow DB_ENGINE=mysql DB_NAME=officium ENABLE_NGINX=true SERVER_NAME=localhost \
    bash "$workspace/deployment/install-officium.sh" install
test "$(stat -c '%U:%G %a' /run/officium)" = "root:officium 750"
runuser -u officium -- /usr/local/bin/officium-update-client integration_check
bash "$here/assert-deployment.sh" v90.0.1

scenario "3. two successive runner updates (catches a self-referencing .env)"
for tag in v90.0.2 v90.0.3; do
    publish "$tag"
    /usr/local/sbin/officium-update-runner --start-latest
    bash "$here/wait-update.sh" success
    bash "$here/assert-deployment.sh" "$tag"
done

scenario "4. admin-button path over IPC as the application user"
publish v90.0.4
cd /
runuser -u officium -- /usr/local/bin/officium-update-client integration_check
runuser -u officium -- python3 - <<'PY'
import json
import socket

request = {'protocol': 1, 'action': 'start_latest_update', 'tag': 'v90.0.4',
           'operation_id': 'upd-0123456789ab', 'requested_by': '1'}
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.settimeout(60)
    client.connect('/run/officium/updater.sock')
    client.sendall(json.dumps(request).encode() + b'\n')
    response = json.loads(client.makefile().readline())
print(response)
assert response['ok'] and response['code'] == 'scheduled', response
PY
bash "$here/wait-update.sh" success
bash "$here/assert-deployment.sh" v90.0.4

scenario "5. a release that cannot start is rolled back automatically"
publish v90.0.5
status=0
officium-update latest --yes || status=$?
echo "officium-update exit code: $status"
test "$status" -eq 1
bash "$here/wait-update.sh" failed
python3 - <<'PY'
import json
status = json.load(open('/var/lib/officium/server-update-status.json'))
assert status['rollback_performed'] is True, status
assert status['error_code'] == 'healthcheck_failed', status
PY
bash "$here/assert-deployment.sh" v90.0.4

scenario "officium-update check, rollback and update again"
officium-update check
officium-update rollback --yes
bash "$here/assert-deployment.sh" v90.0.3
publish v90.0.4
officium-update --tag v90.0.4 --yes
bash "$here/assert-deployment.sh" v90.0.4
ls -l /var/backups/officium /var/log/officium
