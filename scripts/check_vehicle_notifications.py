"""One-command offline QA; uses disposable databases and no Telegram credentials."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-browser', action='store_true', help='Run Python checks only')
    args = parser.parse_args()
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join([str(ROOT), str(ROOT/'tests')])
    env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH','')
    checks = [
        'test_vehicle_notifications.NotificationTests.test_collect_boundaries_privacy_archive',
        'test_vehicle_notifications.NotificationTests.test_audit_batch_and_snooze',
        'test_vehicle_notifications.NotificationTests.test_delivery_dedup_failure_next_day_and_audit',
        'test_vehicle_notifications.NotificationTests.test_flags_and_profile',
        'test_vehicle_notification_runner.NotificationRunnerTests.test_local_only_and_runner_idempotence',
        'test_vehicle_notification_migration.NotificationMigrationTests',
        'test_vehicle_notification_transport.NotificationTransportTests.test_real_adapter_payload_and_dedup',
        'test_vehicle_notification_transport.NotificationTransportTests.test_http_error_is_private_and_not_retried_today',
        'test_vehicle_notification_transport.NotificationTransportTests.test_api_false_is_failure',
        'test_vehicle_notification_systemd.NotificationSystemdTests',
    ]
    subprocess.run([sys.executable,'-m','unittest',*checks,'-v'], cwd=ROOT,env=env,check=True)
    if not args.skip_browser:
        node = shutil.which('node')
        if not node:
            raise SystemExit('Python checks passed. Install Node.js or add node to PATH for browser checks.')
        subprocess.run([node,str(ROOT/'node_modules/@playwright/test/cli.js'),'test',
            'tests/e2e/vehicle-notifications.spec.js','--project=chromium',
            '--output=.tmp/stage6-browser-results','--reporter=line'],cwd=ROOT,env=env,check=True)
    print('Offline checks finished. Real Telegram delivery and live systemd scheduling are not exercised.')


if __name__ == '__main__':
    main()
