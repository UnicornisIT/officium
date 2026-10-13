import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.runner_loader import load_runner


runner = load_runner()

BUILD_ROOT = '/opt/officium/releases/.build-v1-abc/v1-0123456789ab'
FINAL_ROOT = '/opt/officium/releases/v1-0123456789ab'


def make_release(base, root):
    """A release whose venv was created at ``root`` but now sits at ``base``."""
    bin_dir = base / 'venv' / 'bin'
    bin_dir.mkdir(parents=True)
    (bin_dir / 'waitress-serve').write_text(
        f'#!{root}/venv/bin/python\nimport sys\nfrom waitress.runner import run\n', encoding='utf-8'
    )
    (bin_dir / 'activate').write_text(f"VIRTUAL_ENV='{root}/venv'\nexport VIRTUAL_ENV\n", encoding='utf-8')
    (bin_dir / 'activate.fish').write_text(f'set -gx VIRTUAL_ENV "{root}/venv"\n', encoding='utf-8')
    (base / 'venv' / 'pyvenv.cfg').write_text(
        f'home = /usr/bin\ncommand = /usr/bin/python3 -m venv {root}/venv\n', encoding='utf-8'
    )
    return base


class VenvRelocationTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.release = make_release(Path(self.temp.name) / 'release', BUILD_ROOT)
        if not hasattr(os, 'chown'):  # Windows developer machines
            patcher = mock.patch.object(runner.os, 'chown', create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_moved_release_with_build_shebangs_is_rejected(self):
        with self.assertRaises(runner.UpdateFailure) as caught:
            runner.verify_release_venv(self.release, 'venv', expected_root=FINAL_ROOT)
        self.assertEqual(caught.exception.code, 'deployment_failed')
        self.assertIn('waitress-serve', str(caught.exception))
        self.assertIn('.build-v1-abc', str(caught.exception))

    def test_relocation_points_every_script_at_the_final_path(self):
        runner.relocate_release_venv(self.release, BUILD_ROOT, FINAL_ROOT, 'venv')
        runner.verify_release_venv(self.release, 'venv', expected_root=FINAL_ROOT)
        script = (self.release / 'venv' / 'bin' / 'waitress-serve').read_text(encoding='utf-8')
        self.assertTrue(script.startswith(f'#!{FINAL_ROOT}/venv/bin/python\n'))
        self.assertIn(f"VIRTUAL_ENV='{FINAL_ROOT}/venv'", (self.release / 'venv' / 'bin' / 'activate').read_text())
        self.assertIn(f'-m venv {FINAL_ROOT}/venv', (self.release / 'venv' / 'pyvenv.cfg').read_text())
        self.assertNotIn(BUILD_ROOT, (self.release / 'venv' / 'bin' / 'activate.fish').read_text())

    def test_relocation_is_idempotent_and_keeps_unrelated_files(self):
        (self.release / 'venv' / 'bin' / 'notes').write_text('no paths here\n', encoding='utf-8')
        runner.relocate_release_venv(self.release, BUILD_ROOT, FINAL_ROOT, 'venv')
        runner.relocate_release_venv(self.release, BUILD_ROOT, FINAL_ROOT, 'venv')
        runner.verify_release_venv(self.release, 'venv', expected_root=FINAL_ROOT)
        self.assertEqual((self.release / 'venv' / 'bin' / 'notes').read_text(), 'no paths here\n')

    def test_a_sibling_release_path_is_not_accepted_as_inside(self):
        runner.relocate_release_venv(self.release, BUILD_ROOT, FINAL_ROOT + '-old', 'venv')
        with self.assertRaises(runner.UpdateFailure):
            runner.verify_release_venv(self.release, 'venv', expected_root=FINAL_ROOT)

    def test_venv_name_is_detected(self):
        self.assertEqual(runner.release_venv_name(self.release), 'venv')
        with self.assertRaises(runner.UpdateFailure):
            runner.release_venv_name(Path(self.temp.name))


if __name__ == '__main__':
    unittest.main()


def _symlinks_supported():
    with tempfile.TemporaryDirectory() as directory:
        try:
            os.symlink(directory, Path(directory) / 'probe', target_is_directory=True)
        except (OSError, NotImplementedError):
            return False
    return True


class ReleaseEnvironmentLinkTestCase(unittest.TestCase):
    def managed_config(self, app_root):
        return {
            'APP_DIR': f'{app_root}/current', 'APP_USER': 'officium', 'REPOSITORY': 'owner/officium',
            'VENV_NAME': 'venv', 'ENV_FILE': '',
        }

    def test_managed_installs_read_the_shared_env_file(self):
        config = self.managed_config('/opt/officium')
        with mock.patch.object(runner.Path, 'is_symlink', return_value=True):
            self.assertEqual(runner.application_env_path(config), Path('/opt/officium/shared/.env'))
        with mock.patch.object(runner.Path, 'is_symlink', return_value=False):
            legacy = {**config, 'APP_DIR': '/opt/officium'}
            self.assertEqual(runner.application_env_path(legacy), Path('/opt/officium/.env'))
        self.assertEqual(
            runner.application_env_path({**config, 'ENV_FILE': '/etc/officium/app.env'}),
            Path('/etc/officium/app.env'),
        )

    def test_build_command_links_the_release_to_the_shared_file_not_current(self):
        config = self.managed_config('/opt/officium')
        with mock.patch.object(runner.Path, 'is_symlink', return_value=True):
            command = runner.deploy_command(
                config, 'v1.2.3', runuser='/usr/sbin/runuser',
                deploy_script=Path('/usr/local/lib/officium/deploy-release'),
                build_root=Path('/opt/officium/releases/.build-x'),
                stage_path=Path('/tmp/stage'), result_path=Path('/tmp/result'),
            )
        option = {command[index]: command[index + 1] for index in range(5, len(command) - 1, 2)}
        self.assertEqual(Path(option['--environment-file']), Path('/opt/officium/shared/.env'))
        self.assertEqual(option['--environment-link'], option['--environment-file'])
        self.assertNotIn('current', option['--environment-link'])
        self.assertEqual(command[:4], ['/usr/sbin/runuser', '-u', 'officium', '--'])

    def test_build_command_refuses_an_env_file_outside_shared(self):
        config = {**self.managed_config('/opt/officium'), 'ENV_FILE': '/opt/officium/current/.env'}
        with self.assertRaises(runner.UpdateFailure):
            runner.deploy_command(
                config, 'v1.2.3', runuser='runuser', deploy_script=Path('/x'), build_root=Path('/b'),
                stage_path=Path('/s'), result_path=Path('/r'),
            )

    @unittest.skipUnless(_symlinks_supported(), 'symlinks are not available')
    def test_two_successive_updates_keep_current_env_resolvable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'shared').mkdir()
            (root / 'shared' / '.env').write_text('SECRET_KEY=x\n', encoding='utf-8')
            (root / 'releases').mkdir()
            config = self.managed_config(str(root))
            current = root / 'current'

            def install(name, env_link):
                release = root / 'releases' / name
                release.mkdir()
                os.symlink(env_link, release / '.env')
                runner.verify_release_environment_link(release, runner.application_env_path(config))
                temporary = root / '.current.next'
                os.symlink(release, temporary, target_is_directory=True)
                os.replace(temporary, current)

            # N -> N+1 -> N+2 with the link the runner now passes.
            os.symlink(root / 'releases' / 'missing', current, target_is_directory=True)
            for name in ('v1', 'v2', 'v3'):
                install(name, str(root / 'shared' / '.env'))
                runner.verify_current_environment(config)
                self.assertEqual((current / '.env').resolve(strict=True), root / 'shared' / '.env')

            # The previous builder linked the release to current/.env: once
            # current points at that release the link refers to itself.
            release = root / 'releases' / 'v4'
            release.mkdir()
            os.symlink(str(current / '.env'), release / '.env')
            with self.assertRaises(runner.UpdateFailure):
                runner.verify_release_environment_link(release, runner.application_env_path(config))
            temporary = root / '.current.next'
            os.symlink(release, temporary, target_is_directory=True)
            os.replace(temporary, current)
            with self.assertRaises(runner.UpdateFailure) as caught:
                runner.verify_current_environment(config)
            self.assertEqual(caught.exception.code, 'healthcheck_failed')
