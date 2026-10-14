#!/bin/bash
# Scenario 2 on a disposable Ubuntu machine (run as root): the production
# v0.0.7 legacy checkout in /opt/officium, with
#   - a unit that runs /opt/officium/venv/bin/waitress-serve,
#   - a manual drop-in overriding ExecStart with the same console script,
#   - nginx serving `location /static/ { alias /opt/officium/static/; }`,
# migrated with officium-update started from a 0700 directory in /root.

set -euo pipefail

workspace="${1:?usage: scenario-legacy.sh WORKSPACE ENV_FILE}"
env_file="${2:?usage: scenario-legacy.sh WORKSPACE ENV_FILE}"
here="$workspace/tests/integration/deploy"
headers='x-forwarded-for x-forwarded-host x-forwarded-proto x-forwarded-port'
scenario() { printf '\n========== %s ==========\n' "$*"; }

scenario "prepare the v0.0.7 legacy checkout"
git clone --quiet --branch v0.0.7 https://github.com/UnicornisIT/officium.git /opt/officium
install -o root -g officium -m 0640 "$env_file" /opt/officium/.env
python3 -m venv /opt/officium/venv
/opt/officium/venv/bin/python -m pip install --quiet --disable-pip-version-check -r /opt/officium/requirements.txt
chown -R officium:officium /opt/officium
(cd /opt/officium && runuser -u officium -- env FLASK_APP=run.py /opt/officium/venv/bin/python -m flask db upgrade)
cat >/etc/systemd/system/officium.service <<UNIT
[Unit]
Description=Officium (legacy checkout)
After=network-online.target

[Service]
User=officium
Group=officium
WorkingDirectory=/opt/officium
EnvironmentFile=/opt/officium/.env
ExecStart=/opt/officium/venv/bin/waitress-serve --listen=127.0.0.1:5000 --threads=4 --trusted-proxy=127.0.0.1 "--trusted-proxy-headers=$headers" run:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT
install -d /etc/systemd/system/officium.service.d
cat >/etc/systemd/system/officium.service.d/override.conf <<'UNIT'
[Service]
ExecStart=
ExecStart=/opt/officium/venv/bin/waitress-serve --listen=127.0.0.1:5000 run:app
UNIT
cat >/etc/nginx/sites-available/officium <<'NGINX'
server {
    listen 80;
    server_name localhost;
    location /static/ {
        alias /opt/officium/static/;
    }
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
    }
}
NGINX
ln -sfn /etc/nginx/sites-available/officium /etc/nginx/sites-enabled/officium
nginx -t && systemctl restart nginx
systemctl daemon-reload
systemctl enable --now officium.service
sleep 5
curl --silent --show-error --fail --output /dev/null http://127.0.0.1:5000/login

scenario "2. migrate with officium-update from /root/<dir> (mode 0700)"
printf 'v90.0.1\n' >/srv/qa-api/latest
install -d -m 0700 /root/officium-v90.0.1
install -m 0755 "$workspace/scripts/officium-update" /root/officium-v90.0.1/officium-update
cd /root/officium-v90.0.1
status=0
env OFFICIUM_REPOSITORY=qa/officium OFFICIUM_SOURCE_URL=file:///srv/qa-git \
    OFFICIUM_GITHUB_API_URL=http://127.0.0.1:8765 FIX_NGINX=true \
    bash ./officium-update --tag v90.0.1 --yes || status=$?
echo "officium-update exit code: $status"
test "$status" -eq 0
cd /

scenario "legacy leftovers were retired with backups"
test ! -e /etc/systemd/system/officium.service.d/override.conf
find /var/backups/officium -path '*officium.service.d/override.conf' | grep -q .
if grep -RqsE '^[[:space:]]*alias[[:space:]]+/opt/officium' /etc/nginx; then
    echo "nginx still aliases into /opt/officium" >&2
    exit 1
fi
find /var/backups/officium -path '*nginx*' -name officium | grep -q .
compgen -G '/opt/officium.legacy.*' >/dev/null
ls /var/log/officium/update-*.log
if grep -RqsE 'DB_PASSWORD|qa-db-password' /var/log/officium; then
    echo "the update log contains a database secret" >&2
    exit 1
fi
bash "$here/assert-deployment.sh" v90.0.1

scenario "the migrated server updates through the runner"
printf 'v90.0.2\n' >/srv/qa-api/latest
officium-update latest --yes
bash "$here/assert-deployment.sh" v90.0.2
