#!/bin/bash
# Invariants checked after every deployment scenario (run as root):
#   assert-deployment.sh EXPECTED_TAG
# - current is the expected release and realpath(current/.env) is shared/.env
# - every venv/bin file points inside the release
# - the unit and drop-ins run python -m waitress, never a console script
# - the service is active and NRestarts does not grow for 12 seconds
# - the smoke test passes, including /static/css/style.css through nginx

set -euo pipefail

expected="${1:?usage: assert-deployment.sh EXPECTED_TAG}"
app=/opt/officium
step() { printf '\n--- %s\n' "$*"; }

step "current is $expected"
current=$(realpath -e "$app/current")
echo "$current"
[[ "$(basename "$current")" == "$expected"-* ]]
grep -qx "$expected" "$current/VERSION"

step "current/.env is the shared file"
realpath -e "$app/current/.env"
[ "$(realpath -e "$app/current/.env")" = "$app/shared/.env" ]
for release in "$app"/releases/*/; do
    [ -L "$release/.env" ] || continue
    printf '%s -> %s\n' "$release.env" "$(readlink "$release/.env")"
    [ "$(readlink "$release/.env")" = "$app/shared/.env" ]
done

step "venv scripts point inside the release"
/usr/local/sbin/officium-update-runner --verify-release "$current" "$app/shared/.env"

step "no unit runs a venv console script"
systemctl cat officium.service
if systemctl cat officium.service | grep -q waitress-serve; then
    echo "a unit or drop-in still runs waitress-serve" >&2
    exit 1
fi
systemctl show officium.service -p ExecStart --value | grep -q -- '/bin/python -m waitress'

step "service is active and not restarting"
systemctl is-active officium.service
before=$(systemctl show officium.service -p NRestarts --value)
sleep 12
after=$(systemctl show officium.service -p NRestarts --value)
echo "NRestarts $before -> $after"
[ "$before" = "$after" ]

step "post-deploy smoke test"
bash "$current/deployment/post-deploy-smoke.sh" --service officium --app-dir "$app/current" \
    --url http://127.0.0.1:5000/login --timer officium-monthly-expenses.timer --public-host localhost

step "static files through nginx"
curl --silent --show-error --fail --header 'Host: localhost' --dump-header - --output /dev/null \
    http://127.0.0.1/static/css/style.css | tee /dev/stderr | grep -qi '^Content-Type: text/css'
curl --silent --show-error --fail --header 'Host: localhost' --output /dev/null http://127.0.0.1/login

echo "ASSERTIONS PASS for $expected"
