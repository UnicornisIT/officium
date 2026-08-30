import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from app import create_app
from app.models import ActivityLog, User
from app.services import server_update_service
from app.services.server_update_service import (
    ServerUpdateError,
    current_release_has_tag,
    fetch_latest_release,
    read_update_status,
    request_server_update,
    validate_release_tag,
)
from extensions import db


class ServerUpdateServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        directory = Path(self.temporary_directory.name)
        self.helper = directory / 'officium-update-runner'
        self.helper.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        self.helper.chmod(0o755)
        self.config = {
            'APP_VERSION': 'v1.0.0',
            'SERVER_UPDATE_ENABLED': True,
            'SERVER_UPDATE_REPOSITORY': 'UnicornisIT/officium',
            'SERVER_UPDATE_GITHUB_TOKEN': '',
            'SERVER_UPDATE_HELPER': str(self.helper.resolve()),
            'SERVER_UPDATE_USE_SUDO': False,
            'SERVER_UPDATE_APP_DIR': str(directory.resolve()),
            'SERVER_UPDATE_STATUS_PATH': str((directory / 'status.json').resolve()),
            'SERVER_UPDATE_HTTP_TIMEOUT_SECONDS': 8,
            'SERVER_UPDATE_COMMAND_TIMEOUT_SECONDS': 15,
            'SERVER_UPDATE_STALE_MINUTES': 120,
            'SERVER_UPDATE_REQUIRE_ROOT_OWNED_HELPER': False,
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
        }
        return response

    @patch.object(server_update_service.requests, 'get')
    def test_latest_release_uses_fixed_github_api_endpoint(self, get):
        get.return_value = self.github_response()

        release = fetch_latest_release(self.config)

        self.assertEqual(release['tag'], 'v1.1.0')
        self.assertEqual(
            release['url'],
            'https://github.com/UnicornisIT/officium/releases/tag/v1.1.0',
        )
        self.assertEqual(
            get.call_args.args[0],
            'https://api.github.com/repos/UnicornisIT/officium/releases/latest',
        )
        self.assertEqual(
            get.call_args.kwargs['headers']['X-GitHub-Api-Version'],
            '2022-11-28',
        )

    @patch.object(server_update_service.requests, 'get')
    def test_github_failure_is_returned_as_safe_message(self, get):
        get.side_effect = requests.Timeout('internal timeout detail')

        with self.assertRaisesRegex(ServerUpdateError, 'Не удалось проверить') as raised:
            fetch_latest_release(self.config)

        self.assertNotIn('internal timeout detail', str(raised.exception))

    def test_release_tag_rejects_paths_and_shell_characters(self):
        for tag in ('release/v1.0.0', '../v1', 'v1;shutdown', 'v1 2', 'v1.lock'):
            with self.subTest(tag=tag), self.assertRaises(ServerUpdateError):
                validate_release_tag(tag)
        self.assertEqual(validate_release_tag('v1.2.3-rc.1'), 'v1.2.3-rc.1')

    def test_current_commit_can_have_more_than_one_release_tag(self):
        current = {'tag': 'legacy-name', 'tags': ['legacy-name', 'v1.1.0']}

        self.assertTrue(current_release_has_tag(current, 'v1.1.0'))

    @patch.object(server_update_service.requests, 'get')
    def test_disabled_update_never_contacts_github(self, get):
        self.config['SERVER_UPDATE_ENABLED'] = False

        with self.assertRaisesRegex(ServerUpdateError, 'отключено'):
            request_server_update(self.config, 'v1.1.0', requested_by=42)

        get.assert_not_called()

    @patch.object(server_update_service.subprocess, 'run')
    @patch.object(server_update_service.requests, 'get')
    def test_update_runs_only_fixed_helper_with_verified_latest_tag(self, get, run):
        get.return_value = self.github_response()
        run.return_value = Mock(returncode=0, stdout='', stderr='')

        release = request_server_update(self.config, 'v1.1.0', requested_by=42)

        self.assertEqual(release['tag'], 'v1.1.0')
        self.assertEqual(run.call_args.args[0], [str(self.helper.resolve()), 'v1.1.0'])
        self.assertNotIn('shell', run.call_args.kwargs)
        status = read_update_status(self.config)
        self.assertEqual(status['state'], 'queued')
        self.assertEqual(status['requested_by'], '42')

    @patch.object(server_update_service.subprocess, 'run')
    @patch.object(server_update_service.requests, 'get')
    def test_stale_form_tag_cannot_start_update(self, get, run):
        get.return_value = self.github_response(tag='v1.2.0')

        with self.assertRaisesRegex(ServerUpdateError, 'не является последним'):
            request_server_update(self.config, 'v1.1.0', requested_by=42)

        run.assert_not_called()

    @patch.object(server_update_service.subprocess, 'run')
    @patch.object(server_update_service.requests, 'get')
    def test_parallel_update_is_rejected(self, get, run):
        get.return_value = self.github_response()
        Path(self.config['SERVER_UPDATE_STATUS_PATH']).write_text(
            json.dumps({
                'state': 'running',
                'updated_at': server_update_service._iso_now(),
            }),
            encoding='utf-8',
        )

        with self.assertRaisesRegex(ServerUpdateError, 'уже запущено'):
            request_server_update(self.config, 'v1.1.0', requested_by=42)

        run.assert_not_called()

    @patch.object(server_update_service.subprocess, 'run')
    @patch.object(server_update_service.requests, 'get')
    def test_stale_request_lock_is_recovered(self, get, run):
        get.return_value = self.github_response()
        run.return_value = Mock(returncode=0, stdout='', stderr='')
        lock_path = Path(f'{self.config["SERVER_UPDATE_STATUS_PATH"]}.request.lock')
        lock_path.write_text('stale', encoding='utf-8')
        old_time = time.time() - 3 * 60 * 60
        os.utime(lock_path, (old_time, old_time))

        request_server_update(self.config, 'v1.1.0', requested_by=42)

        self.assertFalse(lock_path.exists())
        run.assert_called_once()

    def test_root_runner_source_is_valid_python(self):
        runner = Path(__file__).resolve().parents[1] / 'deployment' / 'officium-update-runner'
        compile(runner.read_text(encoding='utf-8'), str(runner), 'exec')


class ServerUpdateRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'server-update-test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'SERVER_UPDATE_ENABLED': True,
            'SERVER_UPDATE_REPOSITORY': 'UnicornisIT/officium',
            'SERVER_UPDATE_APP_DIR': self.temporary_directory.name,
            'SERVER_UPDATE_STATUS_PATH': str(
                Path(self.temporary_directory.name) / 'status.json'
            ),
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

    @patch('app.routes.admin.get_current_release')
    @patch('app.routes.admin.fetch_latest_release')
    def test_superadmin_sees_latest_release_and_update_button(self, latest, current):
        latest.return_value = {
            'tag': 'v1.1.0',
            'name': 'Release 1.1.0',
            'published_at': '2026-08-30T10:00:00Z',
            'url': 'https://github.com/UnicornisIT/officium/releases/tag/v1.1.0',
        }
        current.return_value = {'label': 'v1.0.0', 'tag': 'v1.0.0', 'commit': 'abc123'}
        self.login(self.superadmin_id)

        response = self.client.get('/admin/server-update')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Обновление сервера', html)
        self.assertIn('Обновить до v1.1.0', html)
        self.assertIn('name="csrf_token"', html)

    def test_ordinary_admin_cannot_open_server_update(self):
        self.login(self.admin_id)

        response = self.client.get('/admin/server-update')

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/admin'))

    def test_status_response_is_not_cached(self):
        self.login(self.superadmin_id)

        response = self.client.get('/admin/server-update/status')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    @patch('app.routes.admin.request_server_update')
    def test_update_post_requires_csrf_token(self, request_update):
        self.login(self.superadmin_id)
        self.app.config['WTF_CSRF_ENABLED'] = True

        response = self.client.post(
            '/admin/server-update/apply',
            data={'tag': 'v1.1.0'},
        )

        self.assertEqual(response.status_code, 400)
        request_update.assert_not_called()

    @patch('app.routes.admin.request_server_update')
    def test_superadmin_request_is_audited(self, request_update):
        request_update.return_value = {'tag': 'v1.1.0'}
        self.login(self.superadmin_id)

        response = self.client.post(
            '/admin/server-update/apply',
            data={'tag': 'v1.1.0'},
        )

        self.assertEqual(response.status_code, 302)
        request_update.assert_called_once()
        with self.app.app_context():
            log = ActivityLog.query.filter_by(action='Запустил обновление сервера').one()
            self.assertIn('v1.1.0', log.description)


if __name__ == '__main__':
    unittest.main()
