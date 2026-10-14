import ast
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


class AppUserWorkingDirectoryTestCase(unittest.TestCase):
    def test_every_command_run_as_the_app_user_starts_in_root(self):
        """runuser keeps the caller's cwd; an unreadable one breaks git and pip."""
        source = Path(runner.__file__).read_text(encoding='utf-8')
        tree = ast.parse(source)
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ('run', 'Popen')
            and isinstance(node.func.value, ast.Name) and node.func.value.id == 'subprocess'
        ]
        app_user_calls = []
        for call in calls:
            segment = ast.get_source_segment(source, call)
            arguments = ast.get_source_segment(source, call.args[0]) if call.args else ''
            if 'runuser' in arguments or arguments == 'command':
                app_user_calls.append(segment)
                keywords = {keyword.arg: keyword.value for keyword in call.keywords}
                self.assertIn('cwd', keywords, segment)
                self.assertEqual(ast.get_source_segment(source, keywords['cwd']), 'APP_USER_CWD')
        self.assertGreaterEqual(len(app_user_calls), 5)
        self.assertEqual(runner.APP_USER_CWD, '/')

    def test_cli_entry_point_leaves_the_operator_directory(self):
        with mock.patch.object(runner.os, 'chdir') as chdir, \
                mock.patch.object(runner.os, 'geteuid', create=True, return_value=1000):
            with self.assertRaises(runner.UpdateFailure):
                runner.main(['officium-update-runner', '--check'])
        chdir.assert_called_once_with('/')


class UpdaterSocketAccessTestCase(unittest.TestCase):
    config = {
        'APP_USER': 'officium', 'APP_GROUP': 'officium', 'UPDATE_SOCKET': '/run/officium/updater.sock',
    }

    def test_probe_connects_as_the_app_user_through_the_real_client(self):
        command = runner.app_user_socket_probe_command(self.config, '/usr/sbin/runuser')
        self.assertEqual(command[:4], ['/usr/sbin/runuser', '-u', 'officium', '--'])
        self.assertIn('OFFICIUM_UPDATE_SOCKET=/run/officium/updater.sock', command)
        self.assertEqual(command[-2:], ['/usr/local/bin/officium-update-client', 'get_status'])

    def test_failed_probe_names_the_directory_permissions(self):
        failed = mock.Mock(returncode=1, stderr='PermissionError: [Errno 13]')
        with mock.patch.object(runner.shutil, 'which', return_value='/usr/sbin/runuser'), \
                mock.patch.object(runner.subprocess, 'run', return_value=failed) as run:
            with self.assertRaises(runner.UpdateFailure) as caught:
                runner.verify_app_user_can_reach_updater(self.config)
        self.assertIn('root:officium 0750', str(caught.exception))
        self.assertEqual(run.call_args.kwargs['cwd'], '/')

    def test_only_the_cli_check_probes_the_socket(self):
        source = Path(runner.__file__).read_text(encoding='utf-8')
        self.assertIn('check_readiness(probe_socket=True)', source)
        self.assertIn('result = check_readiness(emit=False)', source)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class ServiceHealthTestCase(unittest.TestCase):
    def check(self, states, statuses=(200,), **kwargs):
        clock = FakeClock()
        states = list(states)
        statuses = list(statuses)

        def read_properties(_service):
            # Each probe sees the next state; the last one repeats.
            return dict(states.pop(0) if len(states) > 1 else states[0])

        def fetch_status(_url):
            return statuses.pop(0) if len(statuses) > 1 else statuses[0]

        return runner.verify_service_health(
            'officium', '', read_properties=read_properties, fetch_status=fetch_status,
            clock=clock, sleep=clock.sleep, **kwargs,
        )

    def test_stable_service_answering_200_is_healthy(self):
        stable = {'ActiveState': 'active', 'SubState': 'running', 'NRestarts': '0', 'InvocationID': 'a'}
        result = self.check([stable], statuses=(None, 502, 200))
        self.assertEqual(result['url'], 'http://127.0.0.1:5000/login')

    def test_crash_loop_that_is_briefly_active_is_not_healthy(self):
        # 203/EXEC with Restart=on-failure: "active" between restarts.
        states = [
            {'ActiveState': 'active', 'SubState': 'running', 'NRestarts': '3', 'InvocationID': 'a'},
            {'ActiveState': 'activating', 'SubState': 'auto-restart', 'NRestarts': '4', 'InvocationID': 'a',
             'Result': 'exit-code'},
            {'ActiveState': 'active', 'SubState': 'running', 'NRestarts': '4', 'InvocationID': 'b'},
        ]
        with self.assertRaises(runner.UpdateFailure) as caught:
            self.check(states)
        self.assertEqual(caught.exception.code, 'healthcheck_failed')
        self.assertIn('NRestarts 3 -> 4', str(caught.exception))

    def test_restart_with_the_same_counter_is_caught_by_the_invocation_id(self):
        states = [
            {'ActiveState': 'active', 'NRestarts': '0', 'InvocationID': 'a'},
            {'ActiveState': 'active', 'NRestarts': '0', 'InvocationID': 'b'},
        ]
        with self.assertRaises(runner.UpdateFailure):
            self.check(states)

    def test_restart_during_the_http_probe_is_caught(self):
        before = {'ActiveState': 'active', 'NRestarts': '0', 'InvocationID': 'a'}
        after = {'ActiveState': 'active', 'NRestarts': '1', 'InvocationID': 'b'}
        with self.assertRaises(runner.UpdateFailure):
            self.check([before] * 11 + [after])

    def test_the_stable_window_lasts_at_least_ten_seconds(self):
        stable = {'ActiveState': 'active', 'NRestarts': '0', 'InvocationID': 'a'}
        clock = FakeClock()
        runner.verify_service_health(
            'officium', '', read_properties=lambda _service: stable, fetch_status=lambda _url: 200,
            clock=clock, sleep=clock.sleep,
        )
        self.assertGreaterEqual(clock.now, 10)

    def test_http_errors_fail_the_check(self):
        stable = {'ActiveState': 'active', 'NRestarts': '0', 'InvocationID': 'a'}
        with self.assertRaises(runner.UpdateFailure) as caught:
            self.check([stable], statuses=(500,))
        self.assertIn('500', str(caught.exception))
        with self.assertRaises(runner.UpdateFailure):
            self.check([stable], statuses=(302,))

    def test_service_that_never_becomes_active_fails(self):
        failed = {'ActiveState': 'failed', 'SubState': 'failed', 'Result': 'exit-code'}
        with self.assertRaises(runner.UpdateFailure) as caught:
            self.check([failed])
        self.assertIn('failed/failed', str(caught.exception))

    def test_invalid_url_is_a_configuration_error(self):
        with self.assertRaises(runner.UpdateFailure) as caught:
            runner.verify_service_health('officium', 'ftp://example.org/')
        self.assertEqual(caught.exception.code, 'configuration_invalid')


class ReleaseSourceTestCase(unittest.TestCase):
    def test_production_defaults_are_github(self):
        values = {'SOURCE_URL': 'https://github.com/', 'GITHUB_API_URL': 'https://api.github.com'}
        runner.validate_release_sources(values)
        self.assertEqual(runner.repository_url({**values, 'REPOSITORY': 'o/officium'}),
                         'https://github.com/o/officium.git')

    def test_only_a_local_mirror_and_a_loopback_api_are_accepted_for_tests(self):
        runner.validate_release_sources({
            'SOURCE_URL': 'file:///srv/qa-git', 'GITHUB_API_URL': 'http://127.0.0.1:8765',
        })
        for source, api in (
            ('https://gitlab.com', 'https://api.github.com'),
            ('http://github.com', 'https://api.github.com'),
            ('file:///srv/qa git', 'https://api.github.com'),
            ('https://github.com', 'http://10.0.0.5:8765'),
            ('https://github.com', 'http://127.0.0.1:8765/evil'),
        ):
            with self.subTest(source=source, api=api), self.assertRaises(runner.UpdateFailure):
                runner.validate_release_sources({'SOURCE_URL': source, 'GITHUB_API_URL': api})

    def test_checkout_remote_from_the_mirror_matches_the_repository(self):
        self.assertEqual(
            runner.normalize_github_remote('file:///srv/qa-git/qa/officium.git\n', 'file:///srv/qa-git'),
            'qa/officium',
        )
        self.assertEqual(runner.normalize_github_remote('https://github.com/o/officium.git'), 'o/officium')
        self.assertEqual(runner.normalize_github_remote('file:///elsewhere/o/officium.git'), '')


class PrivilegedComponentTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.release = root / 'release'
        (self.release / 'deployment').mkdir(parents=True)
        self.installed = root / 'installed'
        self.installed.mkdir()
        components = (
            ('deployment/officium-update-runner', self.installed / 'officium-update-runner', 'python', True),
            ('deployment/deploy-release', self.installed / 'deploy-release', 'shell', True),
            ('scripts/officium-update', self.installed / 'officium-update', 'shell', False),
        )
        patcher = mock.patch.object(runner, 'PRIVILEGED_COMPONENTS', components)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def test_current_differs_and_missing_components_are_reported(self):
        self.write(self.release / 'deployment' / 'officium-update-runner', 'new runner')
        self.write(self.installed / 'officium-update-runner', 'old runner')
        self.write(self.release / 'deployment' / 'deploy-release', 'deploy')
        states = {item['component']: item['state'] for item in runner.component_status(self.release)}
        self.assertEqual(states, {'officium-update-runner': 'differs', 'deploy-release': 'missing'})

    def test_optional_operator_command_is_checked_only_when_shipped(self):
        for name in ('officium-update-runner', 'deploy-release'):
            self.write(self.release / 'deployment' / name, name)
            self.write(self.installed / name, name)
        self.assertEqual([item['state'] for item in runner.component_status(self.release)], ['current', 'current'])
        self.write(self.release / 'scripts' / 'officium-update', 'operator')
        states = {item['component']: item['state'] for item in runner.component_status(self.release)}
        self.assertEqual(states['officium-update'], 'missing')
        self.assertEqual(len(runner.release_components(self.release)), 3)


class RequestedReleaseTestCase(unittest.TestCase):
    def test_button_path_still_requires_the_latest_release(self):
        with mock.patch.object(runner, 'latest_release_tag', return_value='v2.0.0'):
            with self.assertRaises(runner.UpdateFailure) as caught:
                runner.verify_requested_release({}, 'v1.0.0', True)
        self.assertEqual(caught.exception.code, 'release_rejected')

    def test_pinned_root_update_only_needs_a_published_tag(self):
        with mock.patch.object(runner, 'latest_release_tag') as latest, \
                mock.patch.object(runner, 'verify_tag_published') as published:
            runner.verify_requested_release({}, 'v1.0.0', False)
        latest.assert_not_called()
        published.assert_called_once_with({}, 'v1.0.0')

    def test_cli_start_tag_schedules_a_pinned_update(self):
        with mock.patch.object(runner.os, 'chdir'), \
                mock.patch.object(runner.os, 'geteuid', create=True, return_value=0), \
                mock.patch.object(runner, 'request_background_update') as request:
            self.assertEqual(runner.main(['runner', '--start-tag', 'v1.2.3']), 0)
        request.assert_called_once_with('v1.2.3', require_latest=False)

    def test_ipc_start_keeps_the_latest_only_rule(self):
        source = Path(runner.__file__).read_text(encoding='utf-8')
        ipc = source[source.index('def handle_ipc_request'):source.index('def send_ipc_response')]
        self.assertNotIn('require_latest=False', ipc)


@unittest.skipUnless(_symlinks_supported(), 'symlinks are not available')
class RollbackEnvironmentRepairTestCase(unittest.TestCase):
    def test_old_release_link_through_current_is_repaired(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'shared').mkdir()
            (root / 'shared' / '.env').write_text('SECRET_KEY=x\n', encoding='utf-8')
            release = root / 'releases' / 'v0.0.9-8d77b3168898'
            release.mkdir(parents=True)
            os.symlink(str(root / 'current' / '.env'), release / '.env')
            config = {'APP_DIR': str(root / 'current'), 'ENV_FILE': '', 'APP_GROUP': 'officium'}
            os.symlink(release, root / 'current', target_is_directory=True)
            with mock.patch.object(runner.grp, 'getgrnam', create=True,
                                   return_value=mock.Mock(gr_gid=os.getgid())), \
                    mock.patch.object(runner.os, 'lchown'):
                self.assertTrue(runner.repair_release_environment_link(config, release))
                self.assertFalse(runner.repair_release_environment_link(config, release))
            runner.verify_current_environment(config)
