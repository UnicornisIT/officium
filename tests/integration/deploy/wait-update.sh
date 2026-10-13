#!/bin/bash
# Wait until the runner's status file reaches a final state and compare it:
#   wait-update.sh success|failed [STATUS_FILE]

set -euo pipefail

expected="${1:?usage: wait-update.sh success|failed}"
status_file="${2:-/var/lib/officium/server-update-status.json}"
state=""
for _attempt in $(seq 1 600); do
    state=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("state", ""))' \
        "$status_file" 2>/dev/null || true)
    case "$state" in success|failed) break ;; esac
    sleep 2
done
cat "$status_file"
echo
[ "$state" = "$expected" ] || {
    journalctl -u 'officium-update-*' -u officium.service --no-pager -n 200 || true
    echo "expected $expected, got ${state:-nothing}" >&2
    exit 1
}
