#!/bin/bash
# Free Tier (1 vCPU, 1 GiB RAM, 10 GB disk) resource run on a disposable CI VM:
#   scenario-free-tier.sh WORKSPACE ENV_FILE RESULTS_DIR
#
# Budget emulation, reproducible on any Ubuntu 24.04 runner:
# - CPU: the application slice and the MySQL container are pinned to CPU 0.
# - RAM: officium-budget.slice (web service, housekeeping, updates) gets
#   MemoryMax=640M without swap; the MySQL container 384M with a 64 MiB
#   InnoDB buffer pool. 640 + 384 = 1024 MiB. nginx and the OS are outside
#   both limits and are reported from the host's used memory.
# - Disk: /opt/officium is its own 3 GiB ext4 filesystem (releases, shared/),
#   so the update preflight sees a small disk; a ballast file makes it full.
# Steps: clean install, idle, UI + imports (import_load_driver.py), update,
# update refused for lack of disk, automatic rollback of a broken release,
# disk budget. Peaks per step land in RESULTS_DIR.

set -euo pipefail

workspace="${1:?usage: scenario-free-tier.sh WORKSPACE ENV_FILE RESULTS_DIR}"
env_file="${2:?usage: scenario-free-tier.sh WORKSPACE ENV_FILE RESULTS_DIR}"
results="${3:?usage: scenario-free-tier.sh WORKSPACE ENV_FILE RESULTS_DIR}"
here="$workspace/tests/integration/deploy"
qa=/usr/local/lib/officium-qa
markers="$results/markers.txt"
publish() { printf '%s\n' "$1" >/srv/qa-api/latest; }
scenario() { printf '\n========== %s ==========\n' "$*"; }
mark() { printf '%s %s %s\n' "$(date +%s.%N)" "$1" "$2" >>"$markers"; }
# The release's python as the application user with the application's .env.
app_python() {
    local -a environment
    mapfile -t environment < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' /opt/officium/shared/.env)
    runuser -u officium -- env "${environment[@]}" /opt/officium/current/venv/bin/python "$@"
}
export REPOSITORY=qa/officium SOURCE_URL=file:///srv/qa-git GITHUB_API_URL=http://127.0.0.1:8765
mkdir -p "$results"
: >"$markers"

scenario "budget: CPU 0, 640 MiB application slice, 384 MiB MySQL, 3 GiB /opt/officium"
cat >/etc/systemd/system/officium-budget.slice <<'EOF'
[Unit]
Description=Officium share of a 1 vCPU / 1 GiB Free Tier server (CI emulation)

[Slice]
CPUAccounting=yes
MemoryAccounting=yes
AllowedCPUs=0
MemoryMax=640M
MemorySwapMax=0
EOF
for unit in officium.service officium-maintenance.service officium-monthly-expenses.service; do
    install -d "/etc/systemd/system/$unit.d"
    printf '[Service]\nSlice=officium-budget.slice\n' >"/etc/systemd/system/$unit.d/50-free-tier-budget.conf"
done
systemctl daemon-reload
systemctl start officium-budget.slice
# The dash nests it in officium.slice; take the real path from systemd.
budget_cgroup="/sys/fs/cgroup$(systemctl show officium-budget.slice -p ControlGroup --value)"
echo "budget cgroup: $budget_cgroup"
test -f "$budget_cgroup/memory.current"
test "$(cat "$budget_cgroup/memory.max")" = $((640 * 1024 * 1024))

container=$(docker ps --filter ancestor=mysql:8.4 --format '{{.ID}}' | head -n 1)
[ -n "$container" ] || { echo "MySQL service container not found" >&2; exit 1; }
container=$(docker inspect --format '{{.Id}}' "$container")
mysql -h127.0.0.1 -uroot -pqa-root-password -e "
    SET PERSIST innodb_buffer_pool_size = 67108864;
    SET PERSIST max_connections = 30;
    SET PERSIST_ONLY performance_schema = OFF;"
docker restart "$container" >/dev/null
for _attempt in $(seq 1 60); do
    mysqladmin ping -h127.0.0.1 -uroot -pqa-root-password --silent 2>/dev/null && break
    sleep 2
done
docker update --memory 384m --memory-swap 384m --cpuset-cpus 0 "$container" >/dev/null
test -f "/sys/fs/cgroup/system.slice/docker-$container.scope/memory.current" || \
    test -f "/sys/fs/cgroup/docker/$container/memory.current" || \
    { echo "MySQL container cgroup not found; its memory would not be measured" >&2; exit 1; }
mysql -h127.0.0.1 -uroot -pqa-root-password -e \
    "SELECT @@innodb_buffer_pool_size, @@max_connections, @@performance_schema;"

truncate -s 3G /var/lib/officium-disk.img
mkfs.ext4 -q -F /var/lib/officium-disk.img
install -d -m 0755 /opt/officium
mount -o loop /var/lib/officium-disk.img /opt/officium
df -h /opt/officium

install -d -m 0755 "$qa"
install -m 0644 "$here/resource_sampler.py" "$here/import_probe.py" "$qa/"
install -d -m 0755 "$qa/performance"
install -m 0644 "$workspace/tests/performance/import_load_driver.py" \
    "$workspace/tests/performance/import_memory_profile.py" "$qa/performance/"
python3 "$qa/resource_sampler.py" sample "$results/samples.jsonl" "$budget_cgroup" "$container" &
sampler=$!
trap 'kill "$sampler" 2>/dev/null || true' EXIT

scenario "1. clean install of v90.0.1"
publish v90.0.1
mark start install
started=$(date +%s.%N)
env RELEASE_TAG=v90.0.1 ENV_FILE_SOURCE="$env_file" APP_DIR=/opt/officium APP_USER=officium \
    APP_GROUP=officium SERVICE_NAME=officium HOST=127.0.0.1 PORT=5000 TRUSTED_PROXY=127.0.0.1 \
    APP_TIMEZONE=Europe/Moscow DB_ENGINE=mysql DB_NAME=officium ENABLE_NGINX=true SERVER_NAME=localhost \
    bash "$workspace/deployment/install-officium.sh" install
mark end install
printf 'install_seconds=%s\n' "$(python3 -c "import time; print(round(time.time() - $started, 1))")" >>"$results/summary.txt"
systemctl show officium.service -p Slice --value | grep -qx officium-budget.slice
bash "$here/assert-deployment.sh" v90.0.1

scenario "2. startup and idle"
mark start restart_and_idle
started=$(date +%s.%N)
systemctl restart officium.service
until curl -fs -o /dev/null http://127.0.0.1:5000/login; do sleep 0.2; done
printf 'startup_to_first_200_seconds=%s\n' "$(python3 -c "import time; print(round(time.time() - $started, 2))")" >>"$results/summary.txt"
sleep 20
mark end restart_and_idle
systemctl show officium.service -p MemoryCurrent -p MemoryPeak -p TasksCurrent | tee -a "$results/summary.txt"

scenario "3. UI and imports through the live server (Waitress, nginx not involved)"
cd /opt/officium/current
mark start ui_and_imports
app_python "$qa/performance/import_load_driver.py" mint >"$results/session.json"
chmod 0644 "$results/session.json"
/opt/officium/current/venv/bin/python "$qa/performance/import_load_driver.py" run \
    --base-url http://127.0.0.1:5000 --session "$results/session.json" \
    --cgroup "$budget_cgroup" \
    --storage-dir /opt/officium/shared/instance/bank_imports --out "$results/http-scenarios.json"
mark end ui_and_imports
systemctl show officium.service -p NRestarts -p MemoryPeak | tee -a "$results/summary.txt"
[ "$(systemctl show officium.service -p NRestarts --value)" = 0 ] || { echo "service restarted (OOM?)" >&2; exit 1; }
app_python "$qa/performance/import_load_driver.py" cleanup
cd /

scenario "4. update v90.0.1 -> v90.0.2 inside the budget"
publish v90.0.2
mark start update
systemd-run --wait --quiet --collect --slice=officium-budget.slice --property=Type=exec \
    /usr/local/sbin/officium-update-runner --perform-pinned v90.0.2
mark end update
bash "$here/wait-update.sh" success
bash "$here/assert-deployment.sh" v90.0.2

scenario "5. update refused before any change when the disk is too full"
free_kib=$(df -Pk /opt/officium | awk 'NR==2 {print $4}')
# Leave 600 MiB: above the installer's needs, below release + backup + 750 MiB reserve.
fallocate -l "$(( (free_kib - 600 * 1024) * 1024 ))" /opt/officium/qa-ballast
df -h /opt/officium
before=$(realpath -e /opt/officium/current)
publish v90.0.3
mark start update_refused_low_disk
status=0
systemd-run --wait --quiet --collect --slice=officium-budget.slice --property=Type=exec \
    /usr/local/sbin/officium-update-runner --perform-pinned v90.0.3 || status=$?
mark end update_refused_low_disk
echo "runner exit status: $status"
[ "$status" -ne 0 ]
python3 - <<'PY'
import json
status = json.load(open('/var/lib/officium/server-update-status.json'))
print(status)
assert status['state'] == 'failed' and status['error_code'] == 'insufficient_resources', status
PY
[ "$(realpath -e /opt/officium/current)" = "$before" ]
journalctl --no-pager -n 20 --output=cat | grep 'not enough resources' || true
rm -f /opt/officium/qa-ballast
bash "$here/assert-deployment.sh" v90.0.2

scenario "6. broken release v90.0.5 rolls back automatically"
publish v90.0.5
mark start failed_update_and_rollback
systemd-run --wait --quiet --collect --slice=officium-budget.slice --property=Type=exec \
    /usr/local/sbin/officium-update-runner --perform-pinned v90.0.5 || true
mark end failed_update_and_rollback
python3 - <<'PY'
import json
status = json.load(open('/var/lib/officium/server-update-status.json'))
assert status['rollback_performed'] is True and status['error_code'] == 'healthcheck_failed', status
PY
bash "$here/assert-deployment.sh" v90.0.2

scenario "7. more updates: old releases are pruned to RELEASES_KEEP"
for tag in v90.0.3 v90.0.4; do
    publish "$tag"
    systemd-run --wait --quiet --collect --slice=officium-budget.slice --property=Type=exec \
        /usr/local/sbin/officium-update-runner --perform-pinned "$tag"
    bash "$here/wait-update.sh" success
done
ls -la /opt/officium/releases
releases=$(find /opt/officium/releases -mindepth 1 -maxdepth 1 -type d -name 'v*' | wc -l)
echo "releases kept: $releases"
[ "$releases" -le 3 ]
# The rolled-back v90.0.5 never served; it goes before working rollback targets.
if compgen -G '/opt/officium/releases/v90.0.5-*' >/dev/null; then
    echo "the rolled-back v90.0.5 was kept instead of a working release" >&2
    exit 1
fi
compgen -G '/opt/officium/releases/v90.0.3-*' >/dev/null
bash "$here/assert-deployment.sh" v90.0.4

scenario "8. disk budget"
kill "$sampler" 2>/dev/null || true
trap - EXIT
{
    echo '## Disk usage (du -sm)'
    du -sm /opt/officium/releases/* /opt/officium/shared /var/backups/officium 2>/dev/null
    echo "journal: $(journalctl --disk-usage)"
    echo "mysql datadir: $(docker exec "$container" du -sm /var/lib/mysql | cut -f1) MiB"
    echo "pip cache of the app user: $(du -sm /opt/officium/.cache 2>/dev/null | cut -f1 || echo none)"
    df -h / /opt/officium
} | tee -a "$results/summary.txt"
/usr/local/sbin/officium-update-runner --disk-report | sed -n 's/^OFFICIUM_UPDATE_RESULT=//p' \
    | python3 -m json.tool | tee "$results/disk-report.json"
python3 "$qa/resource_sampler.py" report "$results/samples.jsonl" "$markers" "$results/steps.json" \
    | tee "$results/steps.md"
python3 - "$results/http-scenarios.json" <<'PY' | tee -a "$results/steps.md"
import json, sys
rows = json.load(open(sys.argv[1]))
keys = ('scenario', 'seconds', 'rss_peak_mib', 'cpu_seconds', 'disk_free_delta_mib')
print('\n| ' + ' | '.join(keys) + ' | result |')
print('|' + '---|' * (len(keys) + 1))
for row in rows:
    result = row.get('statuses') or row.get('status') or [row.get('preview_status'), row.get('confirm_status'), row.get('imported')]
    print('| ' + ' | '.join(str(row.get(key)) for key in keys) + f' | {result} {row.get("error", "")} |')
PY
echo "FREE TIER SCENARIO PASS"
