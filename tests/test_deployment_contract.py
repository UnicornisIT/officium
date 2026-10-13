import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractTestCase(unittest.TestCase):
    def test_systemd_unit_uses_run_app_one_quoted_proxy_header_argument(self):
        unit = (PROJECT_ROOT / 'deployment' / 'officium.service.in').read_text(encoding='utf-8')
        exec_start = next(line for line in unit.splitlines() if line.startswith('ExecStart='))
        self.assertIn('"--trusted-proxy-headers=@@TRUSTED_PROXY_HEADERS@@"', exec_start)
        self.assertTrue(exec_start.endswith(' run:app'))
        self.assertNotIn(' app:app', exec_start)
        self.assertIn('NoNewPrivileges=true', unit)
        self.assertIn('ProtectSystem=strict', unit)
        self.assertIn('ProtectHome=read-only', unit)
        self.assertIn('--listen=@@HOST@@:@@PORT@@', exec_start)
        self.assertTrue(exec_start.startswith('ExecStart=@@VENV_DIR@@/bin/python -m waitress '))

    def test_no_unit_or_script_runs_a_venv_console_script(self):
        # Console scripts carry the absolute path of the build directory; after
        # the release moves they fail with 203/EXEC. Only venv/bin/python is
        # path-independent, so everything runs as `venv/bin/python -m <module>`.
        console_script = re.compile(r'(?:VENV_DIR@@|venv|\$VENV_NAME|\{venv\})/bin/(?!python\b)[A-Za-z]')
        # systemd_units.py and nginx_legacy_static.py only *detect* old setups.
        detectors = {'systemd_units.py', 'nginx_legacy_static.py'}
        checked = [
            path for path in (PROJECT_ROOT / 'deployment').iterdir()
            if path.is_file() and path.name not in detectors
        ]
        checked += [PROJECT_ROOT / 'scripts' / 'deploy.sh']
        for path in checked:
            with self.subTest(path=path.name):
                code = '\n'.join(
                    line for line in path.read_text(encoding='utf-8').splitlines()
                    if not line.lstrip().startswith('#')
                )
                self.assertIsNone(console_script.search(code), console_script.search(code))
                self.assertNotIn('waitress-serve', code)

    def test_updater_uses_credential_checked_socket_and_not_sudo(self):
        runner = (PROJECT_ROOT / 'deployment' / 'officium-update-runner').read_text(encoding='utf-8')
        web_client = (PROJECT_ROOT / 'app' / 'services' / 'server_update_service.py').read_text(encoding='utf-8')
        self.assertIn('SO_PEERCRED', runner)
        self.assertIn("'start_latest_update'", runner)
        self.assertIn("'get_status'", runner)
        self.assertIn("'integration_check'", runner)
        self.assertIn('set(request_payload) != schemas[action]', runner)
        self.assertIn("state == 'queued'", runner)
        self.assertIn("config['STALE_MINUTES']", runner)
        self.assertNotIn('GITHUB_TOKEN_FILE', runner)
        self.assertNotIn("dir='/run'", runner)
        self.assertNotIn('sudo', web_client.lower())
        self.assertFalse((PROJECT_ROOT / 'deployment' / 'officium-updater.sudoers').exists())

    def test_updater_socket_directory_is_reachable_by_the_app_group(self):
        installer = (PROJECT_ROOT / 'deployment' / 'install-officium-updater.sh').read_text(encoding='utf-8')
        self.assertIn(
            r'''printf 'd %s 0750 root %s -\n' "$SOCKET_DIR" "$APP_GROUP" >/etc/tmpfiles.d/officium.conf''',
            installer,
        )
        self.assertIn('systemd-tmpfiles --create /etc/tmpfiles.d/officium.conf', installer)
        self.assertIn('chown root:"$APP_GROUP" "$SOCKET_DIR"', installer)
        # The socket is recreated inside the corrected directory.
        self.assertLess(installer.index('systemd-tmpfiles --create'),
                        installer.index('systemctl enable --now officium-updater.socket'))
        self.assertIn('systemctl stop officium-updater.service officium-updater.socket', installer)

    def test_installer_uses_the_runner_health_check_not_a_single_is_active(self):
        installer = (PROJECT_ROOT / 'deployment' / 'install-officium.sh').read_text(encoding='utf-8')
        self.assertIn('/usr/local/sbin/officium-update-runner --verify-health "$SERVICE_NAME"', installer)
        self.assertNotIn('is-active --quiet "$SERVICE_NAME', installer)
        self.assertGreaterEqual(installer.count('service_is_healthy'), 4)

    def test_mysql_backup_avoids_process_privilege_and_command_line_password(self):
        runner = (PROJECT_ROOT / 'deployment' / 'officium-update-runner').read_text(encoding='utf-8')
        for option in ('--single-transaction', '--quick', '--no-tablespaces', '--routines', '--triggers', '--events'):
            self.assertIn(option, runner)
        self.assertIn('verify_database_backup_access(config, db_settings)', runner)
        self.assertIn("f'--defaults-extra-file={credentials_path}', '--no-data'", runner)
        self.assertIn('--defaults-extra-file=', runner)
        self.assertNotIn('MYSQL_PWD', runner)
        self.assertNotIn('--password=', runner)

    def test_release_builder_never_checks_out_over_active_current_tree(self):
        deploy = (PROJECT_ROOT / 'deployment' / 'deploy-release').read_text(encoding='utf-8')
        self.assertIn('mktemp -d "$RELEASES_DIR/.staging-', deploy)
        self.assertIn('git clone --quiet --no-checkout', deploy)
        self.assertIn('requirements-build.txt', deploy)
        self.assertIn('mv -- "$stage_dir/source" "$final_dir"', deploy)
        self.assertIn('Existing release is not root-owned.', deploy)
        self.assertIn('Existing release differs from the verified commit.', deploy)
        self.assertNotIn('git -C "$CURRENT_LINK" checkout', deploy)
        runner = (PROJECT_ROOT / 'deployment' / 'officium-update-runner').read_text(encoding='utf-8')
        self.assertIn('atomic_switch_release', runner)
        self.assertIn('restore_release_link', runner)
        self.assertIn("'rollback_failed'", runner)
        self.assertIn('except Exception as exc:', runner)
        self.assertIn('seal_and_verify_release(config, release_path, tag, release_commit)', runner)
        self.assertIn("build_root = Path(tempfile.mkdtemp(prefix=f'.build-{tag[:24]}-'", runner)
        self.assertIn("RELEASES_DIR must be root-owned and not writable.", runner)
        self.assertIn("git, '--no-replace-objects'", runner)
        self.assertIn("source_info.st_uid != 0 or source_info.st_mode & 0o222", runner)
        self.assertIn('install_privileged_bundle', runner)
        self.assertIn("'/usr/local/bin/officium-update-client', 'integration_check'", runner)
        self.assertIn('os.replace(temporary, target)', runner)

    def test_installer_is_parameterized_and_rejects_non_loopback_waitress(self):
        installer = (PROJECT_ROOT / 'deployment' / 'install-officium.sh').read_text(encoding='utf-8')
        for name in (
            'APP_DIR', 'APP_USER', 'APP_GROUP', 'VENV_DIR', 'SERVICE_NAME', 'HOST',
            'PORT', 'TRUSTED_PROXY', 'APP_TIMEZONE', 'DB_ENGINE', 'DB_NAME', 'BACKUP_DIR',
            'UPDATE_SOCKET',
        ):
            self.assertIn(f'{name}="${{{name}:-', installer)
        self.assertIn('Waitress must bind to loopback', installer)
        self.assertIn('configure_managed_env', installer)
        self.assertIn('app.env.before-installer.', installer)
        self.assertIn('release_script_dir="$APP_DIR/releases/$release_name/deployment"', installer)
        self.assertIn('seal_release "$staged_release" "$RELEASE_TAG"', installer)
        self.assertIn('chown -hR "root:$APP_GROUP" "$release_real"', installer)
        self.assertIn('build_root=$(mktemp -d "$APP_DIR/releases/.build-install.', installer)
        self.assertIn('install -d -o root -g "$APP_GROUP" -m 0750 "$APP_DIR/releases"', installer)
        self.assertIn('Initial activation failed; no current release is active.', installer)
        self.assertIn('unlink "$APP_DIR/current"', installer)
        self.assertIn('systemctl reload nginx', installer)
        self.assertNotIn('SCRIPT_DIR="$legacy_root/deployment"', installer)
        self.assertIn('/bin/sh "$release_script_dir/install-officium-updater.sh"', installer)
        self.assertIn('finish_with_summary "Legacy migration" "$release_script_dir" "$legacy_root"', installer)

    def test_legacy_migration_finishes_with_health_smoke_ipc_and_a_summary(self):
        installer = (PROJECT_ROOT / 'deployment' / 'install-officium.sh').read_text(encoding='utf-8')
        summary = installer[installer.index('finish_with_summary() {'):installer.index('seal_release() {')]
        # Smoke test before the IPC check; an IPC failure only warns.
        self.assertLess(summary.index('post-deploy-smoke.sh'), summary.index('integration_check'))
        self.assertIn('button_status="NOT ready"', summary)
        for field in ('Version:', 'current:', 'Site:', 'Update button:', 'Backups:', 'Legacy checkout:'):
            self.assertIn(field, summary)
        self.assertIn('[ "$smoke_status" = PASS ] || exit 2', summary)
        legacy = installer[installer.index('# Legacy in-place checkouts'):]
        self.assertLess(legacy.index('service_is_healthy;'), legacy.index('finish_with_summary'))
        self.assertIn('restore_units "$unit_backup"', legacy)
        self.assertIn('retire_stale_dropins "$release_script_dir" "$unit_backup"', legacy)
        self.assertIn('exit 10', legacy)
        self.assertNotIn('\n    runuser -u "$APP_USER" -- /usr/local/bin/officium-update-client integration_check\n', legacy)
        self.assertNotIn('/var/www/debt_manager', installer)

    def test_nginx_templates_keep_waitress_private_and_forward_https_metadata(self):
        production = (PROJECT_ROOT / 'deployment' / 'nginx-officium.conf.in').read_text(encoding='utf-8')
        staging_tls = (PROJECT_ROOT / 'deployment' / 'nginx-officium-tls-staging.conf').read_text(encoding='utf-8')
        self.assertIn('proxy_pass http://@@HOST@@:@@PORT@@', production)
        self.assertIn('proxy_set_header X-Forwarded-Proto $scheme', production)
        self.assertIn('listen 443 ssl', staging_tls)
        self.assertIn('proxy_pass http://127.0.0.1:5000', staging_tls)
        self.assertIn('proxy_set_header X-Forwarded-Proto https', staging_tls)
        # nginx (www-data) cannot read the root:officium 0750 release tree;
        # Flask serves /static/ through the proxy.
        for template in (production, staging_tls):
            code = '\n'.join(line for line in template.splitlines() if not line.lstrip().startswith('#'))
            self.assertNotIn('location /static/', code)
            self.assertNotIn('alias ', code)
        installer = (PROJECT_ROOT / 'deployment' / 'install-officium.sh').read_text(encoding='utf-8')
        self.assertIn('check_nginx_app_paths "$release_script_dir"', installer)
        self.assertIn('if [ "$FIX_NGINX" = true ]; then', installer)

    def test_managed_sqlite_is_shared_and_legacy_database_is_copied_safely(self):
        installer = (PROJECT_ROOT / 'deployment' / 'install-officium.sh').read_text(encoding='utf-8')
        runner = (PROJECT_ROOT / 'deployment' / 'officium-update-runner').read_text(encoding='utf-8')
        self.assertIn('SQLITE_PATH must be absolute and stored below', installer)
        self.assertIn('source.backup(target)', installer)
        self.assertIn('managed shared directory', runner)

    def test_real_waitress_accepts_proxy_header_list_as_one_argument(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=(os.name == 'nt')) as temp_dir:
            temp_path = Path(temp_dir)
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            env = os.environ.copy()
            env.update({
                'OFFICIUM_ENV': 'development',
                'SECRET_KEY': 'waitress-runtime-test-secret-over-32-characters',
                'DB_ENGINE': 'sqlite',
                'SQLITE_PATH': str(temp_path / 'waitress.sqlite'),
                'SESSION_COOKIE_SECURE': 'false',
                'SERVER_UPDATE_ENABLED': 'false',
            })
            # Same invocation as the systemd unit: python -m waitress.
            command = [
                sys.executable, '-m', 'waitress', f'--listen=127.0.0.1:{port}', '--threads=1',
                '--trusted-proxy=127.0.0.1',
                '--trusted-proxy-headers=x-forwarded-for x-forwarded-host x-forwarded-proto x-forwarded-port',
                'run:app',
            ]
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                deadline = time.monotonic() + 20
                last_error = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        output = process.stdout.read() if process.stdout else ''
                        self.assertNotIn('Specify one application only', output)
                        if os.name == 'nt' and 'WinError 10013' in output:
                            # The managed Windows sandbox can deny loopback bind;
                            # argument parsing still reached server creation. CI
                            # performs the complete HTTP launch on Ubuntu.
                            return
                        self.fail(f'Waitress exited early ({process.returncode}): {output}')
                    try:
                        response = requests.get(f'http://127.0.0.1:{port}/login', timeout=1)
                        if response.status_code == 200:
                            break
                    except requests.RequestException as exc:
                        last_error = exc
                    time.sleep(0.2)
                else:
                    self.fail(f'Waitress did not become ready: {last_error}')
                self.assertIn('officium', response.text)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if process.stdout:
                    process.stdout.close()


if __name__ == '__main__':
    unittest.main()
