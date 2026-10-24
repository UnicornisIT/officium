"""Updater resource preflight, old-release cleanup and the disk report."""

import os
import shutil
import tempfile
import time
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

from tests.runner_loader import load_runner

runner = load_runner()
DiskUsage = namedtuple('DiskUsage', 'total used free')
MIB = 1024 * 1024
OWNER = getattr(os, 'getuid', lambda: 0)()


class ResourceLayoutTestCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='officium-resources-'))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.releases = self.root / 'releases'
        self.releases.mkdir()
        (self.root / 'shared').mkdir()
        (self.root / 'shared' / '.env').write_text(
            f'DB_ENGINE=sqlite\nSQLITE_PATH={self.root / "shared" / "officium.sqlite"}\n', encoding='utf-8')
        (self.root / 'shared' / 'officium.sqlite').write_bytes(b'\0' * (3 * MIB))
        self.backups = self.root / 'backups'
        self.backups.mkdir()
        (self.root / 'state').mkdir()
        self.config = {
            'APP_DIR': '',
            'RELEASES_DIR': str(self.releases),
            'BACKUP_DIR': str(self.backups),
            'ENV_FILE': str(self.root / 'shared' / '.env'),
            'STATUS_FILE': str(self.root / 'state' / 'server-update-status.json'),
            'RELEASES_KEEP': 3,
            'UPDATE_MIN_FREE_MB': 750,
            'UPDATE_MIN_MEMORY_MB': 200,
            'DISK_WARN_FREE_MB': 1536,
        }

    def release(self, name, age, size=MIB):
        path = self.releases / name
        path.mkdir()
        (path / 'payload.bin').write_bytes(b'\0' * size)
        marker = path / '.officium-release-ready'
        marker.write_text('0' * 40 + '\n', encoding='utf-8')
        stamp = time.time() - age
        os.utime(marker, (stamp, stamp))
        return path


class PruneReleasesTestCase(ResourceLayoutTestCase):
    def test_keeps_current_previous_and_newest(self):
        oldest = self.release('v1.0.0-000000000001', 500)
        older = self.release('v1.0.1-000000000002', 400)
        previous = self.release('v1.0.2-000000000003', 300)
        newer = self.release('v1.0.3-000000000004', 200)
        current = self.release('v1.0.4-000000000005', 100)
        self.config['APP_DIR'] = str(current)
        result = runner.prune_releases(self.config, protected=(previous,), release_owner_uid=OWNER)
        self.assertEqual(sorted(item['name'] for item in result['removed']), [oldest.name, older.name])
        for path in (previous, newer, current):
            self.assertTrue(path.is_dir(), path.name)
        self.assertFalse(oldest.exists())
        self.assertFalse(older.exists())

    def test_protected_old_release_survives_and_counts_toward_keep(self):
        rollback_target = self.release('v1.0.0-000000000001', 900)
        self.release('v1.0.1-000000000002', 300)
        self.release('v1.0.2-000000000003', 200)
        current = self.release('v1.0.3-000000000004', 100)
        self.config['APP_DIR'] = str(current)
        result = runner.prune_releases(self.config, protected=(rollback_target,), release_owner_uid=OWNER)
        self.assertTrue(rollback_target.is_dir())
        self.assertEqual([item['name'] for item in result['removed']], ['v1.0.1-000000000002'])

    def test_unknown_incomplete_and_foreign_entries_are_never_removed(self):
        current = self.release('v2.0.0-00000000000a', 10)
        for index in range(4):
            self.release(f'v1.0.{index}-00000000000{index}', 1000 + index)
        incomplete = self.releases / 'v0.9.0-0000000000ff'
        incomplete.mkdir()
        unknown = self.releases / 'notes'
        unknown.mkdir()
        loose_file = self.releases / 'v0.8.0-0000000000ee'
        loose_file.write_text('x', encoding='utf-8')
        fresh_build = self.releases / '.build-v2-abc'
        fresh_build.mkdir()
        old_build = self.releases / '.build-v1-def'
        old_build.mkdir()
        stamp = time.time() - runner.STALE_BUILD_SECONDS - 60
        os.utime(old_build, (stamp, stamp))
        self.config['APP_DIR'] = str(current)
        result = runner.prune_releases(self.config, release_owner_uid=OWNER)
        removed = {item['name'] for item in result['removed']}
        self.assertIn(old_build.name, removed)
        for path in (incomplete, unknown, loose_file, fresh_build, current):
            self.assertTrue(path.exists(), path.name)
        self.assertEqual(len(result['kept']), 3)

    def test_release_that_never_served_goes_before_a_working_rollback_target(self):
        working_old = self.release('v1.0.1-000000000001', 400)
        working = self.release('v1.0.2-000000000002', 300)
        broken = self.release('v1.0.5-000000000005', 50)  # built last, rolled back
        current = self.release('v1.0.3-000000000003', 100)
        for release in (working_old, working, current):
            runner.record_release_activation(self.config, release)
            time.sleep(0.01)
        self.config['APP_DIR'] = str(current)
        result = runner.prune_releases(self.config, protected=(working,), release_owner_uid=OWNER)
        self.assertEqual([item['name'] for item in result['removed']], [broken.name])
        self.assertTrue(working_old.is_dir())

    def test_releases_of_another_owner_are_left_alone(self):
        current = self.release('v2.0.0-00000000000a', 10)
        for index in range(4):
            self.release(f'v1.0.{index}-00000000000{index}', 1000 + index)
        self.config['APP_DIR'] = str(current)
        result = runner.prune_releases(self.config, release_owner_uid=OWNER + 12345)
        self.assertEqual(result['removed'], [])

    def test_dry_run_changes_nothing(self):
        current = self.release('v2.0.0-00000000000a', 10)
        old = [self.release(f'v1.0.{index}-00000000000{index}', 1000 + index) for index in range(4)]
        self.config['APP_DIR'] = str(current)
        result = runner.prune_releases(self.config, dry_run=True, release_owner_uid=OWNER)
        self.assertEqual(len(result['removed']), 2)
        self.assertTrue(all(path.is_dir() for path in old))

    def test_keep_below_two_is_rejected_by_the_config_loader(self):
        source = Path(runner.__file__).read_text(encoding='utf-8')
        self.assertIn("('RELEASES_KEEP', 2, 20)", source)


class UpdateResourcePlanTestCase(ResourceLayoutTestCase):
    def setUp(self):
        super().setUp()
        self.current = self.release('v1.0.0-000000000001', 10, size=4 * MIB)
        self.config['APP_DIR'] = str(self.current)

    def plan_with(self, free_mib, mem_mib=800, swap_mib=0):
        usage = DiskUsage(10 * 1024 * MIB, 0, free_mib * MIB)
        with patch.object(runner.shutil, 'disk_usage', return_value=usage), \
                patch.object(runner, 'available_memory', return_value=(mem_mib * MIB, swap_mib * MIB)):
            return runner.verify_update_resources(self.config)

    def test_enough_space_and_memory_passes(self):
        plan = self.plan_with(free_mib=3000)
        filesystem = plan['filesystems'][0]
        # One filesystem: release (min 256 MiB) + SQLite backup + 750 MiB reserve.
        self.assertEqual(len(plan['filesystems']), 1)
        self.assertGreaterEqual(filesystem['needed_mib'], 256 + 3 + 750)
        self.assertTrue(plan['memory']['ok'])

    def test_low_disk_stops_the_update_before_any_change(self):
        with self.assertRaises(runner.UpdateFailure) as caught:
            self.plan_with(free_mib=900)
        self.assertEqual(caught.exception.code, 'insufficient_resources')
        self.assertEqual(sorted(path.name for path in self.backups.iterdir()), [])

    def test_low_memory_stops_the_update(self):
        with self.assertRaises(runner.UpdateFailure) as caught:
            self.plan_with(free_mib=5000, mem_mib=60, swap_mib=0)
        self.assertEqual(caught.exception.code, 'insufficient_resources')

    def test_swap_alone_does_not_make_up_for_missing_ram(self):
        with self.assertRaises(runner.UpdateFailure):
            self.plan_with(free_mib=5000, mem_mib=50, swap_mib=2048)
        self.assertTrue(self.plan_with(free_mib=5000, mem_mib=120, swap_mib=512)['memory']['ok'])

    def test_backup_estimate_uses_the_last_mysql_dump(self):
        (self.root / 'shared' / '.env').write_text('DB_ENGINE=mysql\nDB_NAME=officium\n', encoding='utf-8')
        dump = self.backups / 'officium-v1-20260101T000000Z.sql'
        dump.write_bytes(b'-' * (40 * MIB))
        size, basis = runner.estimate_backup_bytes(self.config)
        self.assertGreaterEqual(size, 50 * MIB)
        self.assertIn(dump.name, basis)

    def test_runner_checks_resources_before_the_backup(self):
        source = Path(runner.__file__).read_text(encoding='utf-8')
        perform = source[source.index('def perform_update('):]
        self.assertLess(perform.index('verify_update_resources(config)'), perform.index('create_backups(config, tag)'))
        self.assertLess(perform.index('create_backups(config, tag)'), perform.index('prune_releases('))


class DiskReportTestCase(ResourceLayoutTestCase):
    def test_report_levels(self):
        current = self.release('v1.0.0-000000000001', 10)
        self.config['APP_DIR'] = str(current)
        for free_mib, level in ((4000, 'ok'), (1200, 'warning'), (600, 'critical')):
            usage = DiskUsage(10 * 1024 * MIB, 0, free_mib * MIB)
            with patch.object(runner.shutil, 'disk_usage', return_value=usage), \
                    patch.object(runner, 'journal_disk_usage', return_value=None), \
                    patch.object(runner, 'available_memory', return_value=None):
                report = runner.disk_report(self.config)
            self.assertEqual(report['level'], level)
            self.assertEqual(report['releases'][0]['name'], current.name)
            self.assertTrue(report['releases'][0]['current'])


if __name__ == '__main__':
    unittest.main()
