import importlib.machinery
import importlib.util
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
runner = None
if os.name != 'nt':
    loader = importlib.machinery.SourceFileLoader(
        'officium_update_runner', str(ROOT / 'deployment' / 'officium-update-runner')
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    runner = importlib.util.module_from_spec(spec)
    loader.exec_module(runner)


@unittest.skipIf(os.name == 'nt', 'The privileged runner is a Linux/systemd component.')
class UpdateRunnerLinuxTestCase(unittest.TestCase):
    def test_stale_active_status_no_longer_blocks_updates_forever(self):
        fresh = {'state': 'queued', 'updated_at': runner.iso_now()}
        stale_time = runner.utc_now() - timedelta(minutes=121)
        stale = {
            'state': 'migrating',
            'updated_at': stale_time.isoformat().replace('+00:00', 'Z'),
        }
        self.assertTrue(runner.status_is_active(fresh, 120))
        self.assertFalse(runner.status_is_active(stale, 120))

    def test_new_queue_and_success_clear_fields_from_previous_failure(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            status_path = Path(temporary_directory) / 'status.json'
            config = {'STATUS_FILE': str(status_path), 'APP_GROUP': 'officium'}
            with patch.object(runner.grp, 'getgrnam', return_value=SimpleNamespace(gr_gid=1)), \
                    patch.object(runner.os, 'chown'):
                runner.write_status(
                    config, 'failed', 'v1.0.0', 'failed',
                    error_code='restart_failed', rollback_performed=True,
                )
                runner.write_status(
                    config, 'queued', 'v1.1.0', 'queued', operation_id='upd-aabbccddeeff'
                )
                queued = runner.read_status(status_path)
                self.assertNotIn('error_code', queued)
                self.assertNotIn('rollback_performed', queued)
                runner.write_status(config, 'success', 'v1.1.0', 'complete')
                successful = runner.read_status(status_path)
                self.assertEqual(successful['operation_id'], 'upd-aabbccddeeff')
                self.assertNotIn('error_code', successful)


if __name__ == '__main__':
    unittest.main()
