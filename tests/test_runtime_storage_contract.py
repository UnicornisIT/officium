"""Deployment contract of the writable runtime storage and the housekeeping timer."""

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT = PROJECT_ROOT / 'deployment'
HARDENING = (
    'NoNewPrivileges=true', 'PrivateTmp=true', 'ProtectHome=read-only', 'ProtectSystem=strict',
    'ReadWritePaths=@@APP_ROOT@@/shared',
)


def settings(text):
    return [line for line in text.splitlines() if line and not line.startswith('#')]


class RuntimeStorageContractTestCase(unittest.TestCase):
    def test_web_service_still_writes_only_to_shared(self):
        unit = (DEPLOYMENT / 'officium.service.in').read_text(encoding='utf-8')
        for line in HARDENING:
            self.assertIn(line, settings(unit))
        self.assertEqual([line for line in settings(unit) if line.startswith('ReadWritePaths=')],
                         ['ReadWritePaths=@@APP_ROOT@@/shared'])
        self.assertNotIn('chmod 777', (DEPLOYMENT / 'install-officium.sh').read_text(encoding='utf-8'))

    def test_maintenance_unit_has_the_web_service_hardening(self):
        unit = settings((DEPLOYMENT / 'officium-maintenance.service').read_text(encoding='utf-8'))
        for line in HARDENING:
            self.assertIn(line, unit)
        self.assertIn('User=@@APP_USER@@', unit)
        self.assertIn('Type=oneshot', unit)
        commands = [line for line in unit if line.startswith('ExecStart=')]
        self.assertEqual(commands, [
            'ExecStart=-@@VENV_DIR@@/bin/python -m flask import-cleanup',
            'ExecStart=@@VENV_DIR@@/bin/python -m flask storage-check',
        ])
        timer = (DEPLOYMENT / 'officium-maintenance.timer').read_text(encoding='utf-8')
        self.assertIn('Unit=@@MAINTENANCE_SERVICE@@.service', timer)
        self.assertIn('OnUnitActiveSec=1h', timer)

    def test_installer_renders_verifies_and_enables_the_timer(self):
        installer = (DEPLOYMENT / 'install-officium.sh').read_text(encoding='utf-8')
        self.assertIn('MAINTENANCE_SERVICE="${MAINTENANCE_SERVICE:-officium-maintenance}"', installer)
        self.assertIn('-e "s|@@MAINTENANCE_SERVICE@@|$MAINTENANCE_SERVICE|g"', installer)
        self.assertIn('"/etc/systemd/system/$MAINTENANCE_SERVICE.timer"', installer)
        # Every systemd-analyze call goes through verify_units (all unit files).
        self.assertEqual(installer.count('systemd-analyze verify'), 1)
        self.assertGreaterEqual(installer.count('verify_units'), 4)
        self.assertGreaterEqual(installer.count('mapfile -t timers < <(timer_units)'), 3)

    def test_installer_points_the_app_at_shared_instance_with_tight_modes(self):
        installer = (DEPLOYMENT / 'install-officium.sh').read_text(encoding='utf-8')
        self.assertIn('set_env_value "$managed_env" OFFICIUM_INSTANCE_PATH "$APP_DIR/shared/instance"', installer)
        self.assertIn('install -d -o "$APP_USER" -g "$APP_GROUP" -m 0750 "$1/shared/instance"', installer)
        self.assertIn('install -d -o "$APP_USER" -g "$APP_GROUP" -m 0700 "$1/shared/instance/bank_imports"', installer)
        # install, upgrade, legacy migration and units all create them.
        self.assertGreaterEqual(installer.count('ensure_runtime_dirs "'), 4)
        # shared/ itself stays root-owned.
        self.assertIn('install -d -o root -g "$APP_GROUP" -m 0750 "$APP_DIR/shared"', installer)

    def test_release_build_uses_wheels_without_a_pip_cache(self):
        script = (DEPLOYMENT / 'deploy-release').read_text(encoding='utf-8')
        installs = re.findall(r'-m pip install[^\n]*', script)
        self.assertEqual(len(installs), 2)
        for line in installs:
            self.assertIn('--no-cache-dir', line)
            self.assertIn('--only-binary=:all:', line)

    def test_operator_backup_covers_the_maintenance_units(self):
        script = (PROJECT_ROOT / 'scripts' / 'officium-update').read_text(encoding='utf-8')
        self.assertIn('/etc/systemd/system/officium-maintenance.timer', script)

    def test_runtime_settings_are_documented_in_the_example_env(self):
        example = (PROJECT_ROOT / '.env.example').read_text(encoding='utf-8')
        for key in (
            'IMPORT_PREVIEW_TTL_MINUTES', 'IMPORT_STORAGE_MAX_BYTES', 'IMPORT_STORAGE_MAX_FILES',
            'IMPORT_MAX_PENDING_PER_USER', 'IMPORT_DISK_RESERVE_BYTES', 'IMPORT_MAX_CONCURRENT_PARSES',
            'IMPORT_MAX_ROWS', 'IMPORT_MAX_SOURCE_ROWS', 'DISK_WARN_FREE_BYTES', 'DISK_CRITICAL_FREE_BYTES',
            'OFFICIUM_INSTANCE_PATH',
        ):
            self.assertIn(f'{key}=', example)
        updater = (DEPLOYMENT / 'updater.env.example').read_text(encoding='utf-8')
        for key in ('RELEASES_KEEP', 'UPDATE_MIN_FREE_MB', 'UPDATE_MIN_MEMORY_MB', 'DISK_WARN_FREE_MB'):
            self.assertIn(f'{key}=', updater)


if __name__ == '__main__':
    unittest.main()
