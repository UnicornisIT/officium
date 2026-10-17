"""The "what's new" window shown once per user after the server is updated."""

import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import create_app
from app.models import User
from app.services import whats_new_service
from app.services.whats_new_service import (
    build_release_notes,
    load_release_notes,
    normalize_version,
    pending_release_notes,
    should_show,
    split_sections,
)
from extensions import db


ROOT = Path(__file__).resolve().parents[1]

CHANGELOG = """# Changelog

## [Unreleased]

### Добавлено

- Ещё не выпущено.

## [9.9.9-alpha] - 2026-10-18

Вступление релиза.

### Не проверено перед выпуском

Служебная заметка.

### Добавлено

- Окно «Что нового».

### Изменено

- Кнопки обновления.

### Исправлено

- Плитка «Панель администратора».

### Известные ограничения

- Внутреннее.

## [9.9.8] - 2026-10-17

### Fixed

- Old fix.

## [9.9.7] - 2026-10-16

### Не проверено перед выпуском

Только служебный раздел.

## [9.9.6] - 2026-10-15
"""


class VersionTestCase(unittest.TestCase):
    def test_normalize_version_strips_the_tag_prefix(self):
        self.assertEqual(normalize_version('v0.0.15-alpha'), '0.0.15-alpha')
        self.assertEqual(normalize_version(' 0.0.13\n'), '0.0.13')
        self.assertIsNone(normalize_version('unknown'))
        self.assertIsNone(normalize_version(''))
        self.assertIsNone(normalize_version(None))
        self.assertIsNone(normalize_version('1.2.3-' + 'a' * 80))


class ChangelogParserTestCase(unittest.TestCase):
    def test_section_with_alpha_version_has_added_changed_fixed_in_order(self):
        notes = build_release_notes(CHANGELOG, 'v9.9.9-alpha')

        self.assertEqual(notes['version'], '9.9.9-alpha')
        self.assertEqual(notes['released_on'], '2026-10-18')
        self.assertEqual([s['kind'] for s in notes['sections']], ['added', 'changed', 'fixed'])
        self.assertEqual([s['title'] for s in notes['sections']], ['Добавлено', 'Изменено', 'Исправлено'])
        self.assertIn('Окно «Что нового».', notes['sections'][0]['html'])
        html = ''.join(s['html'] for s in notes['sections'])
        self.assertNotIn('Служебная заметка', html)
        self.assertNotIn('Внутреннее', html)
        self.assertNotIn('Ещё не выпущено', html)

    def test_english_group_names_are_accepted(self):
        notes = build_release_notes(CHANGELOG, '9.9.8')

        self.assertEqual([(s['kind'], s['title']) for s in notes['sections']], [('fixed', 'Исправлено')])

    def test_missing_section_gives_nothing(self):
        self.assertIsNone(build_release_notes(CHANGELOG, '1.0.0'))
        self.assertIsNone(build_release_notes(CHANGELOG, 'Unreleased'))
        self.assertIsNone(build_release_notes('', '9.9.9-alpha'))
        self.assertIsNone(build_release_notes(None, '9.9.9-alpha'))

    def test_empty_section_or_one_without_user_groups_gives_nothing(self):
        self.assertIsNone(build_release_notes(CHANGELOG, '9.9.6'))
        self.assertIsNone(build_release_notes(CHANGELOG, '9.9.7'))

    def test_group_without_items_is_skipped(self):
        self.assertEqual(split_sections('### Добавлено\n\n### Исправлено\n\n- Да.'), {'fixed': '- Да.'})

    def test_repeated_group_is_merged(self):
        groups = split_sections('### Изменено\n- А\n### Изменено\n- Б')
        self.assertEqual(groups['changed'], '- А\n\n- Б')

    def test_raw_html_from_the_changelog_is_not_rendered(self):
        text = '## [1.0.0] - 2026-01-01\n\n### Добавлено\n\n- <script>alert(1)</script> [x](javascript:alert(1))\n'
        html = build_release_notes(text, '1.0.0')['sections'][0]['html']

        self.assertNotIn('<script', html)
        self.assertNotIn('href="javascript', html)

    def test_the_bundled_changelog_describes_the_current_version(self):
        version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
        notes = load_release_notes(version, ROOT / 'CHANGELOG.md')

        self.assertIsNotNone(notes)
        self.assertEqual(notes['version'], normalize_version(version))


class ChangelogFileTestCase(unittest.TestCase):
    def setUp(self):
        whats_new_service._cache.clear()
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'CHANGELOG.md'

    def tearDown(self):
        whats_new_service._cache.clear()
        self.directory.cleanup()

    def test_missing_file_gives_nothing(self):
        self.assertIsNone(load_release_notes('9.9.9-alpha', self.path))

    def test_oversized_file_is_ignored(self):
        self.path.write_bytes(CHANGELOG.encode('utf-8') + b' ' * whats_new_service.MAX_CHANGELOG_BYTES)
        self.assertIsNone(load_release_notes('9.9.9-alpha', self.path))

    def test_invalid_utf8_does_not_fail(self):
        self.path.write_bytes(CHANGELOG.encode('utf-8') + b'\xff\xfe')
        self.assertEqual(load_release_notes('9.9.9-alpha', self.path)['version'], '9.9.9-alpha')

    def test_unreadable_file_gives_nothing(self):
        self.path.write_text(CHANGELOG, encoding='utf-8')
        with mock.patch.object(Path, 'open', side_effect=PermissionError('denied')):
            self.assertIsNone(load_release_notes('9.9.9-alpha', self.path))


class ShowDecisionTestCase(unittest.TestCase):
    def test_same_version_is_not_shown(self):
        self.assertFalse(should_show('9.9.9-alpha', 'v9.9.9-alpha'))
        self.assertFalse(should_show('v9.9.9-alpha', 'v9.9.9-alpha'))

    def test_existing_user_without_a_mark_or_with_an_older_one_sees_it(self):
        self.assertTrue(should_show(None, 'v9.9.9-alpha'))
        self.assertTrue(should_show('9.9.8', 'v9.9.9-alpha'))

    def test_unknown_current_version_is_never_shown(self):
        self.assertFalse(should_show(None, 'unknown'))
        self.assertFalse(should_show(None, ''))

    def test_errors_never_reach_the_caller(self):
        user = mock.Mock(last_seen_release=None)
        with mock.patch.object(whats_new_service, 'load_release_notes', side_effect=RuntimeError('boom')):
            with self.assertLogs(whats_new_service.logger, 'ERROR'):
                self.assertIsNone(pending_release_notes(user, 'v9.9.9-alpha'))


class _WhatsNewApp:
    csrf = False

    def setUp(self):
        whats_new_service._cache.clear()
        self.directory = tempfile.TemporaryDirectory()
        changelog = Path(self.directory.name) / 'CHANGELOG.md'
        changelog.write_text(CHANGELOG, encoding='utf-8')
        self.changelog_patch = mock.patch.object(whats_new_service, 'CHANGELOG_PATH', changelog)
        self.changelog_patch.start()
        self.app = create_app({
            'TESTING': True,
            'ENVIRONMENT': 'testing',
            'SECRET_KEY': 'whats-new-secret-over-32-characters!',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': self.csrf,
            'APP_VERSION': 'v9.9.9-alpha',
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            fresh = User(telegram_id=1, username='fresh', role='user')
            existing = User(telegram_id=2, username='existing', role='user')
            admin = User(telegram_id=3, username='admin', role='admin')
            db.session.add_all([fresh, existing, admin])
            db.session.commit()
            # Accounts that predate the feature have no mark at all.
            existing.last_seen_release = None
            admin.last_seen_release = None
            db.session.commit()
            self.ids = {'fresh': fresh.id, 'existing': existing.id, 'admin': admin.id}

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        self.changelog_patch.stop()
        self.directory.cleanup()
        whats_new_service._cache.clear()

    def _login(self, key):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.ids[key])
            session['_fresh'] = True

    def _seen(self, key):
        with self.app.app_context():
            return db.session.get(User, self.ids[key]).last_seen_release



class WhatsNewRoutesTestCase(_WhatsNewApp, unittest.TestCase):
    def test_new_user_is_created_at_the_running_version(self):
        self.assertEqual(self._seen('fresh'), '9.9.9-alpha')
        self._login('fresh')

        html = self.client.get('/').get_data(as_text=True)

        self.assertNotIn('id="whatsNewModal"', html)

    def test_existing_user_sees_the_current_release_once(self):
        self._login('existing')

        html = self.client.get('/').get_data(as_text=True)

        self.assertIn('id="whatsNewModal"', html)
        self.assertIn('Что нового в v9.9.9-alpha', html)
        self.assertEqual(re.findall(r'data-whats-new-section="(\w+)"', html), ['added', 'changed', 'fixed'])
        self.assertIn('Понятно', html)
        self.assertNotIn('Напомнить позже', html)
        self.assertNotIn('Old fix', html)

        response = self.client.post('/whats-new/seen', headers={'Accept': 'application/json'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'success': True, 'version': '9.9.9-alpha'})
        self.assertEqual(self._seen('existing'), '9.9.9-alpha')
        self.assertNotIn('id="whatsNewModal"', self.client.get('/').get_data(as_text=True))

    def test_admin_sees_it_in_the_cabinet_but_not_in_the_panel(self):
        self._login('admin')

        self.assertIn('id="whatsNewModal"', self.client.get('/').get_data(as_text=True))
        self.assertNotIn('id="whatsNewModal"', self.client.get('/admin').get_data(as_text=True))

    def test_form_post_redirects_back_to_a_local_path_only(self):
        self._login('existing')

        response = self.client.post('/whats-new/seen', data={'next': '/goals?tab=1'})
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers['Location'].endswith('/goals?tab=1'))

        for unsafe in ('//evil.example/', 'https://evil.example/', '/\\evil.example'):
            response = self.client.post('/whats-new/seen', data={'next': unsafe})
            self.assertEqual(response.status_code, 303)
            self.assertNotIn('evil', response.headers['Location'])

    def test_acknowledging_requires_login(self):
        response = self.client.post('/whats-new/seen')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_get_is_not_allowed(self):
        self._login('existing')
        self.assertEqual(self.client.get('/whats-new/seen').status_code, 405)
        self.assertIsNone(self._seen('existing'))

    def test_session_on_behalf_of_a_user_neither_shows_nor_records(self):
        self._login('existing')
        with self.client.session_transaction() as session:
            session['original_admin_id'] = str(self.ids['admin'])
            session['impersonated_user_id'] = str(self.ids['existing'])
        record = mock.Mock(
            id=7, ended_at=None, read_only=False, target_label='existing',
            expires_at=mock.Mock(isoformat=mock.Mock(return_value='2026-10-18T00:00:00')),
        )
        with mock.patch('app.routes.auth.impersonation.current_record', return_value=record),                 mock.patch('app.routes.auth.impersonation.is_expired', return_value=False),                 mock.patch('app.routes.auth.impersonation.minutes_left', return_value=10),                 mock.patch('app.routes.auth.impersonation.record_change') as record_change:
            page = self.client.get('/')
            response = self.client.post('/whats-new/seen', headers={'Accept': 'application/json'})

        self.assertEqual(page.status_code, 200)
        self.assertNotIn('id="whatsNewModal"', page.get_data(as_text=True))
        self.assertEqual(response.status_code, 403)
        record_change.assert_not_called()
        self.assertIsNone(self._seen('existing'))

    def test_release_without_section_shows_nothing(self):
        self.app.config['APP_VERSION'] = 'v1.0.0'
        self._login('existing')

        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('id="whatsNewModal"', response.get_data(as_text=True))

    def test_broken_changelog_does_not_break_the_page(self):
        self._login('existing')
        with mock.patch.object(whats_new_service, 'build_release_notes', side_effect=ValueError('bad')):
            with self.assertLogs(whats_new_service.logger, 'ERROR'):
                response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('id="whatsNewModal"', response.get_data(as_text=True))

    def test_database_failure_while_saving_is_reported(self):
        self._login('existing')
        from sqlalchemy.exc import OperationalError
        with mock.patch('app.routes.whats_new.db.session.commit', side_effect=OperationalError('x', {}, Exception('down'))):
            response = self.client.post('/whats-new/seen', headers={'Accept': 'application/json'})

        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.get_json()['success'])


class WhatsNewCsrfTestCase(_WhatsNewApp, unittest.TestCase):
    csrf = True

    def _token(self):
        html = self.client.get('/').get_data(as_text=True)
        return re.search(r'<meta name="csrf-token" content="([^"]+)"', html).group(1)

    def test_post_without_token_is_rejected_and_records_nothing(self):
        self._login('existing')
        self.client.get('/')

        response = self.client.post('/whats-new/seen', headers={'Accept': 'application/json'})

        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self._seen('existing'))

    def test_post_with_header_token_records_the_version(self):
        self._login('existing')
        token = self._token()

        response = self.client.post(
            '/whats-new/seen', headers={'Accept': 'application/json', 'X-CSRFToken': token},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._seen('existing'), '9.9.9-alpha')

    def test_the_modal_form_carries_its_own_token(self):
        self._login('existing')
        html = self.client.get('/').get_data(as_text=True)
        form = re.search(r'<form method="post" action="/whats-new/seen".*?</form>', html, re.S).group(0)
        token = re.search(r'name="csrf_token" value="([^"]+)"', form).group(1)

        response = self.client.post('/whats-new/seen', data={'csrf_token': token, 'next': '/'})

        self.assertEqual(response.status_code, 303)
        self.assertEqual(self._seen('existing'), '9.9.9-alpha')


class ReleaseContentsTestCase(unittest.TestCase):
    def test_changelog_is_tracked_and_shipped_with_the_release(self):
        tracked = subprocess.run(
            ['git', 'ls-files', '--error-unmatch', 'CHANGELOG.md', 'VERSION'],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        if tracked.returncode != 0 and 'not a git repository' in tracked.stderr:
            self.skipTest('not a git checkout')
        self.assertEqual(tracked.returncode, 0, tracked.stderr)

        attributes = subprocess.run(
            ['git', 'check-attr', 'export-ignore', '--', 'CHANGELOG.md'],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
        self.assertTrue(attributes.strip().endswith('unspecified'), attributes)

        # Releases are full checkouts of the tag, not an allowlist of files.
        for script in ('deployment/deploy-release', 'scripts/officium-update'):
            text = (ROOT / script).read_text(encoding='utf-8')
            self.assertIn('git clone --quiet --no-checkout', text, script)
            self.assertNotIn('sparse-checkout', text, script)
            self.assertNotIn('git archive', text, script)


if __name__ == '__main__':
    unittest.main()
