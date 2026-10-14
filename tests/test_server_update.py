import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from app import create_app
from app.models import ActivityLog, User
from app.services import server_update_service
from app.services.server_update_service import (
    format_release_date,
    ServerUpdateError,
    check_update_readiness,
    current_release_has_tag,
    fetch_latest_release,
    get_current_release,
    get_update_status,
    read_update_status,
    reconcile_update_status,
    request_server_update,
    validate_release_tag,
)
from extensions import db


class FakeSocket:
    def __init__(self, response):
        self.response = bytearray(response)
        self.sent = b''
        self.connected_to = None
        self.timeout = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, path):
        self.connected_to = path

    def sendall(self, value):
        self.sent += value

    def recv(self, size):
        result = bytes(self.response[:size])
        del self.response[:size]
        return result


class ServerUpdateServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        directory = Path(self.temporary_directory.name)
        self.config = {
            'APP_VERSION': 'v1.0.0',
            'SERVER_UPDATE_ENABLED': True,
            'SERVER_UPDATE_REPOSITORY': 'example-owner/officium',
            'SERVER_UPDATE_SOCKET': str(directory / 'updater.sock'),
            'SERVER_UPDATE_APP_DIR': str(directory),
            'SERVER_UPDATE_STATUS_PATH': str(directory / 'status.json'),
            'SERVER_UPDATE_HTTP_TIMEOUT_SECONDS': 8,
            'SERVER_UPDATE_IPC_TIMEOUT_SECONDS': 8,
            'SERVER_UPDATE_STALE_MINUTES': 120,
        }

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def github_response(tag='v1.1.0'):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            'tag_name': tag,
            'name': 'Release 1.1.0',
            'published_at': '2026-08-30T10:00:00Z',
            'body': '  Security fixes\n\nand deployment hardening.  ',
        }
        return response

    @patch.object(server_update_service.requests, 'get')
    def test_latest_release_uses_fixed_github_api_endpoint(self, get):
        get.return_value = self.github_response()
        release = fetch_latest_release(self.config)
        self.assertEqual(release['tag'], 'v1.1.0')
        self.assertEqual(
            get.call_args.args[0],
            'https://api.github.com/repos/example-owner/officium/releases/latest',
        )
        self.assertNotIn('example-owner', get.call_args.kwargs['headers'].get('Authorization', ''))
        self.assertEqual(
            str(release['notes_html']),
            '<p>Security fixes</p>\n<p>and deployment hardening.</p>',
        )
        self.assertFalse(release['notes_truncated'])
        self.assertEqual(release['published_at'], '2026-08-30T10:00:00Z')

    def test_release_date_is_formatted_in_the_app_timezone(self):
        try:
            ZoneInfo('Europe/Moscow')
        except ZoneInfoNotFoundError:
            pass  # Windows without the tzdata package has no IANA zones.
        else:
            self.assertEqual(
                format_release_date('2026-10-13T12:16:45Z', 'Europe/Moscow'),
                '13 октября 2026, 15:16',
            )
        self.assertEqual(format_release_date('2026-01-01T00:05:00Z', 'UTC'), '1 января 2026, 00:05')
        self.assertEqual(format_release_date('2026-01-01T00:05:00Z', 'No/Such_Zone'), '1 января 2026, 00:05')
        self.assertEqual(format_release_date('not a date'), '')
        self.assertEqual(format_release_date(''), '')

    def test_newer_installed_release_supersedes_only_an_older_failed_status(self):
        failed = {'state': 'failed', 'tag': 'v1.1.0', 'error_code': 'restart_failed'}
        current = {'label': 'v1.2.0', 'tag': 'v1.2.0', 'tags': ['v1.2.0']}
        reconciled = reconcile_update_status(failed, current)
        self.assertEqual(reconciled['state'], 'success')
        self.assertTrue(reconciled['superseded'])
        self.assertEqual(reconciled['tag'], 'v1.2.0')

        still_relevant = reconcile_update_status(
            {'state': 'failed', 'tag': 'v1.3.0'}, current
        )
        self.assertEqual(still_relevant['state'], 'failed')

    def test_uncomparable_failed_status_is_never_hidden(self):
        status = {'state': 'failed', 'tag': 'release-candidate'}
        current = {'label': 'v2.0.0', 'tag': 'v2.0.0', 'tags': ['v2.0.0']}
        self.assertIs(reconcile_update_status(status, current), status)

    @patch.object(server_update_service.requests, 'get')
    def test_github_failure_is_returned_as_safe_message(self, get):
        get.side_effect = requests.Timeout('secret transport detail')
        with self.assertRaisesRegex(ServerUpdateError, 'Не удалось проверить') as raised:
            fetch_latest_release(self.config)
        self.assertNotIn('secret transport detail', str(raised.exception))

    def test_release_tag_rejects_paths_and_shell_characters(self):
        for tag in ('release/v1.0.0', '../v1', 'v1;shutdown', 'v1 2', 'v1.lock'):
            with self.subTest(tag=tag), self.assertRaises(ServerUpdateError):
                validate_release_tag(tag)
        self.assertEqual(validate_release_tag('v1.2.3-rc.1'), 'v1.2.3-rc.1')

    def test_current_commit_can_have_more_than_one_release_tag(self):
        self.assertTrue(current_release_has_tag(
            {'tag': 'legacy', 'tags': ['legacy', 'v1.1.0']}, 'v1.1.0'
        ))

    @patch.object(server_update_service.subprocess, 'run')
    def test_git_tag_takes_precedence_over_stale_env_version(self, run):
        (Path(self.config['SERVER_UPDATE_APP_DIR']) / '.git').mkdir()
        run.side_effect = [
            Mock(returncode=0, stdout='v1.1.0\n'),
            Mock(returncode=0, stdout='abc123def456\n'),
        ]
        current = get_current_release(self.config)
        self.assertEqual(current['label'], 'v1.1.0')
        self.assertEqual(current['commit'], 'abc123def456')
        for call in run.call_args_list:
            self.assertIn('--no-replace-objects', call.args[0])
            self.assertTrue(any(str(arg).startswith('safe.directory=') for arg in call.args[0]))

    @patch.object(server_update_service.requests, 'get')
    def test_disabled_update_never_contacts_github_or_ipc(self, get):
        self.config['SERVER_UPDATE_ENABLED'] = False
        with patch.object(server_update_service, '_ipc_request') as ipc, self.assertRaisesRegex(
            ServerUpdateError, 'отключено'
        ):
            request_server_update(self.config, 'v1.1.0', requested_by=42)
        get.assert_not_called()
        ipc.assert_not_called()

    @patch.object(server_update_service, '_ipc_request')
    @patch.object(server_update_service.requests, 'get')
    def test_update_uses_fixed_ipc_operation_and_never_sudo(self, get, ipc):
        get.return_value = self.github_response()
        ipc.return_value = {'ok': True, 'code': 'scheduled', 'operation_id': 'upd-aabbccddeeff'}
        release = request_server_update(self.config, 'v1.1.0', requested_by=42)
        self.assertEqual(release['operation_id'], 'upd-aabbccddeeff')
        args, fields = ipc.call_args
        self.assertEqual(args[1], 'start_latest_update')
        self.assertEqual(fields['tag'], 'v1.1.0')
        self.assertEqual(fields['requested_by'], '42')
        self.assertRegex(fields['operation_id'], r'^upd-[0-9a-f]{12}$')

    @patch.object(server_update_service, '_ipc_request')
    @patch.object(server_update_service.requests, 'get')
    def test_stale_form_tag_never_reaches_ipc(self, get, ipc):
        get.return_value = self.github_response('v1.2.0')
        with self.assertRaisesRegex(ServerUpdateError, 'не является последним'):
            request_server_update(self.config, 'v1.1.0', requested_by=42)
        ipc.assert_not_called()

    def test_ipc_protocol_sends_only_declared_json_fields(self):
        fake = FakeSocket(b'{"ok":true,"code":"ready"}\n')
        with patch.object(server_update_service.socket, 'AF_UNIX', 1, create=True), patch.object(
            server_update_service.socket, 'socket', return_value=fake
        ):
            result = check_update_readiness(self.config, integration=True)
        self.assertEqual(result['code'], 'ready')
        self.assertEqual(fake.connected_to, self.config['SERVER_UPDATE_SOCKET'])
        self.assertEqual(
            json.loads(fake.sent.decode('ascii')),
            {'protocol': 1, 'action': 'integration_check'},
        )

    def test_ipc_error_is_safe_and_keeps_operation_id(self):
        fake = FakeSocket(
            b'{"ok":false,"code":"backup_failed","operation_id":"upd-aabbccddeeff"}\n'
        )
        with patch.object(server_update_service.socket, 'AF_UNIX', 1, create=True), patch.object(
            server_update_service.socket, 'socket', return_value=fake
        ), self.assertRaisesRegex(ServerUpdateError, 'upd-aabbccddeeff') as raised:
            server_update_service._ipc_request(self.config, 'get_status')
        self.assertIn('резервную копию', str(raised.exception))

    def test_ipc_rejection_is_logged_with_code_and_operation_id(self):
        fake = FakeSocket(
            b'{"ok":false,"code":"unexpected_failure","operation_id":"upd-aabbccddeeff"}\n'
        )
        with patch.object(server_update_service.socket, 'AF_UNIX', 1, create=True), patch.object(
            server_update_service.socket, 'socket', return_value=fake
        ), self.assertLogs(server_update_service.logger, 'WARNING') as logs, self.assertRaises(
            ServerUpdateError
        ):
            server_update_service._ipc_request(self.config, 'get_status')
        self.assertIn('code=unexpected_failure operation_id=upd-aabbccddeeff', logs.output[0])

    def test_start_request_waits_for_runner_release_checks(self):
        fake = FakeSocket(b'{"ok":true,"code":"scheduled","operation_id":"upd-aabbccddeeff"}\n')
        with patch.object(server_update_service.socket, 'AF_UNIX', 1, create=True), patch.object(
            server_update_service.socket, 'socket', return_value=fake
        ):
            server_update_service._ipc_request(
                self.config, 'start_latest_update', tag='v1.1.0',
                operation_id='upd-aabbccddeeff', requested_by='42',
            )
        self.assertGreaterEqual(fake.timeout, 45)

    def test_status_falls_back_to_read_only_file_when_ipc_is_unavailable(self):
        Path(self.config['SERVER_UPDATE_STATUS_PATH']).write_text(
            json.dumps({'state': 'migrating', 'operation_id': 'upd-persisted123'}),
            encoding='utf-8',
        )
        with patch.object(server_update_service, '_ipc_request', side_effect=ServerUpdateError('no')):
            status = get_update_status(self.config)
        self.assertEqual(status['operation_id'], 'upd-persisted123')

    def test_root_runner_and_socket_client_are_valid_python(self):
        root = Path(__file__).resolve().parents[1]
        for relative in ('deployment/officium-update-runner', 'deployment/officium-update-client'):
            source = root / relative
            compile(source.read_text(encoding='utf-8'), str(source), 'exec')

    def test_web_update_service_contains_no_sudo_escalation(self):
        source = Path(server_update_service.__file__).read_text(encoding='utf-8')
        self.assertNotIn('sudo', source.lower())
        self.assertIn('AF_UNIX', source)


class ServerUpdateRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        status_path = Path(self.temporary_directory.name) / 'status.json'
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'server-update-test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'SERVER_UPDATE_ENABLED': True,
            'SERVER_UPDATE_REPOSITORY': 'example-owner/officium',
            'SERVER_UPDATE_APP_DIR': self.temporary_directory.name,
            'SERVER_UPDATE_STATUS_PATH': str(status_path),
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            superadmin = User(telegram_id=-1001, username='root-admin', role='superadmin')
            admin = User(telegram_id=-1002, username='ordinary-admin', role='admin')
            db.session.add_all([superadmin, admin])
            db.session.commit()
            self.superadmin_id = superadmin.id
            self.admin_id = admin.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        self.temporary_directory.cleanup()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True

    @patch('app.routes.admin.get_update_status', return_value=None)
    @patch('app.routes.admin.check_update_readiness', return_value={'ok': True, 'code': 'ready'})
    @patch('app.routes.admin.get_current_release')
    @patch('app.routes.admin.fetch_latest_release')
    def test_superadmin_sees_latest_release_and_update_button(self, latest, current, _check, _status):
        latest.return_value = {
            'tag': 'v1.1.0', 'name': 'Release 1.1.0', 'published_at': '',
            'url': 'https://github.com/example-owner/officium/releases/tag/v1.1.0',
        }
        current.return_value = {'label': 'v1.0.0', 'tag': 'v1.0.0', 'commit': 'abc123'}
        self.login(self.superadmin_id)
        response = self.client.get('/admin/server-update')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Обновить до v1.1.0', html)
        self.assertIn('IPC из Flask', html)
        self.assertIn('name="csrf_token"', html)

    def test_ordinary_admin_cannot_open_server_update(self):
        self.login(self.admin_id)
        self.assertEqual(self.client.get('/admin/server-update').status_code, 403)

    @patch('app.routes.admin.get_update_status', return_value=None)
    def test_status_response_is_not_cached(self, _status):
        self.login(self.superadmin_id)
        response = self.client.get('/admin/server-update/status')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    @patch('app.routes.admin.request_server_update')
    def test_update_post_requires_csrf_token(self, request_update):
        self.login(self.superadmin_id)
        self.app.config['WTF_CSRF_ENABLED'] = True
        response = self.client.post('/admin/server-update/apply', data={'tag': 'v1.1.0'})
        self.assertEqual(response.status_code, 400)
        request_update.assert_not_called()

    @patch('app.routes.admin.request_server_update')
    def test_superadmin_request_is_audited_with_operation(self, request_update):
        request_update.return_value = {'tag': 'v1.1.0', 'operation_id': 'upd-aabbccddeeff'}
        self.login(self.superadmin_id)
        response = self.client.post('/admin/server-update/apply', data={'tag': 'v1.1.0'})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            log = ActivityLog.query.filter_by(action='Запустил обновление сервера').one()
            self.assertIn('v1.1.0', log.description)


if __name__ == '__main__':
    unittest.main()
