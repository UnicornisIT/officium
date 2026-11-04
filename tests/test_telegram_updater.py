import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.runner_loader import load_runner


runner = load_runner()


class TelegramUpdaterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.release = self.root / 'current'
        (self.release / 'deployment').mkdir(parents=True)
        self.helper = self.release / 'deployment' / 'telegram_services.py'
        self.helper.write_text('# test helper\n', encoding='utf-8')
        (self.root / 'shared').mkdir()
        self.marker = self.root / 'shared' / '.telegram-services-release'
        self.config = dict(APP_DIR=str(self.release), APP_USER='custom_user', APP_GROUP='custom_group',
                           SERVICE_NAME='custom', VENV_NAME='.venv', ENV_FILE=str(self.root / 'shared' / '.env'))

    def test_activation_uses_sealed_release_helper_and_custom_settings(self):
        with patch.object(runner.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            runner.sync_telegram_services(self.config, self.release)
        command = run.call_args.args[0]
        self.assertEqual(command[1], str(self.helper))
        self.assertIn('custom_user', command)
        self.assertIn('.venv', command)
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_activation_failure_and_timeout_are_sanitized(self):
        for result in (SimpleNamespace(returncode=1, stderr='private proxy credentials'),
                       subprocess.TimeoutExpired('private command', 1)):
            with self.subTest(result=type(result)), patch.object(runner.subprocess, 'run',
                                                                 side_effect=result if isinstance(result, Exception) else None, return_value=result):
                with self.assertRaises(runner.UpdateFailure) as caught:
                    runner.sync_telegram_services(self.config, self.release)
                self.assertNotIn('private', str(caught.exception))

    def test_first_old_updater_transition_schedules_outside_ipc_sandbox(self):
        with patch.object(runner.shutil, 'which', return_value='/usr/bin/systemd-run'), \
                patch.object(runner.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            runner.schedule_telegram_reconciliation(self.config)
        command = run.call_args.args[0]
        self.assertEqual(command[0], '/usr/bin/systemd-run')
        self.assertEqual(command[-1], '--sync-telegram')
        self.assertIn('--property=Type=exec', command)
        self.assertNotIn('--wait', command)

    def test_first_install_before_current_exists_does_not_schedule(self):
        with patch.object(runner.subprocess, 'run') as run:
            runner.schedule_telegram_reconciliation({**self.config, 'APP_DIR': str(self.root / 'not-installed')})
        run.assert_not_called()

    def test_reconciliation_locks_checks_trust_and_skips_completed_release(self):
        original_stat = Path.stat

        def owned_stat(path, *args, **kwargs):
            result = list(original_stat(path, *args, **kwargs))
            if path in (self.release, self.helper, self.marker):
                result[4] = 0
                result[0] &= ~0o222
            return os.stat_result(result)

        flock = Mock()
        with patch.object(runner, 'LOCK_PATH', self.root / 'lock'), \
                patch.object(runner, 'fcntl', SimpleNamespace(flock=flock, LOCK_EX=2)), \
                patch.object(runner.Path, 'stat', new=owned_stat), \
                patch.object(runner, 'sync_telegram_services') as sync:
            self.assertTrue(runner.reconcile_telegram_services(self.config))
            flock.assert_called_once()
            sync.assert_called_once()
            self.marker.write_text(str(self.release.resolve()) + '\n', encoding='utf-8')
            self.assertTrue(runner.reconcile_telegram_services(self.config))
            with patch.object(runner.subprocess, 'run') as run:
                runner.schedule_telegram_reconciliation(self.config)
                run.assert_not_called()
            sync.assert_called_once()

    def test_root_only_cli_supports_one_time_reconciliation(self):
        with patch.object(runner.os, 'chdir'), patch.object(runner.os, 'geteuid', create=True, return_value=0), \
                patch.object(runner, 'load_runner_config', return_value=self.config), \
                patch.object(runner, 'reconcile_telegram_services', return_value=True) as sync:
            self.assertEqual(runner.main(['runner', '--sync-telegram']), 0)
        sync.assert_called_once_with(self.config)
