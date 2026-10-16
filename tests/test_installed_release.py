import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, text

from app import create_app
from app.models import User
from app.services import installed_release_service, server_update_service
from app.services.installed_release_service import (
    MAX_CHANGELOG_BYTES,
    changelog_version,
    find_installed_release_notes,
    get_database_revision,
    get_installed_release,
    parse_changelog,
)
from app.services.server_update_service import get_commit_details, get_last_successful_update
from extensions import db


CHANGELOG = '''# Changelog

## [Unreleased]

### Изменено

- Черновик следующей версии.

## [0.0.12] - 2026-10-15

### Исправлено

- Расход с датой в другом месяце больше не пропадает.

## [0.0.11 - 2026-10-14

- Битый заголовок.

## [0.0.10] - 2026-13-40

- Невозможная дата.

## [0.0.9] - 2026-10-13

### Добавлено

- Страница обновления сервера.
'''


class InstalledReleaseServiceTestCase(unittest.TestCase):
    def setUp(self):
        installed_release_service._cache.clear()
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.config = {
            'SERVER_UPDATE_APP_DIR': str(self.directory),
            'SERVER_UPDATE_REPOSITORY': 'example-owner/officium',
            'SERVER_UPDATE_STATUS_PATH': str(self.directory / 'status.json'),
            'APP_TIMEZONE': 'UTC',
        }

    def tearDown(self):
        installed_release_service._cache.clear()
        self.temporary_directory.cleanup()

    def write_changelog(self, content=CHANGELOG):
        path = self.directory / 'CHANGELOG.md'
        path.write_text(content, encoding='utf-8')
        return path

    def test_tag_maps_to_changelog_version(self):
        self.assertEqual(changelog_version('v0.0.12'), '0.0.12')
        self.assertEqual(changelog_version('1.2.3-rc.1'), '1.2.3-rc.1')
        self.assertIsNone(changelog_version('Unreleased'))
        self.assertIsNone(changelog_version('../v0.0.12'))
        self.assertIsNone(changelog_version(None))

    def test_installed_version_section_is_found_with_release_date(self):
        self.write_changelog()
        notes = find_installed_release_notes(self.config, 'v0.0.12')
        self.assertEqual(notes['version'], '0.0.12')
        self.assertEqual(notes['released_on'], '2026-10-15')
        self.assertEqual(notes['released_label'], '15 октября 2026')
        self.assertIn('<h4>Исправлено</h4>', notes['notes_html'])
        self.assertIn('больше не пропадает', notes['notes_html'])
        self.assertNotIn('Черновик', notes['notes_html'])
        self.assertNotIn('Страница обновления', notes['notes_html'])

    def test_alpha_tag_finds_its_changelog_section(self):
        self.write_changelog(
            '# Changelog\n\n## [Unreleased]\n\n## [0.0.14-alpha] - 2026-10-16\n\n'
            '### Изменено\n\n- Переводы в цели больше не доходы и не расходы.\n\n'
            '## [0.0.13] - 2026-10-15\n\n- Старое.\n'
        )
        self.assertEqual(changelog_version('v0.0.14-alpha'), '0.0.14-alpha')
        notes = find_installed_release_notes(self.config, 'v0.0.14-alpha')
        self.assertEqual(notes['version'], '0.0.14-alpha')
        self.assertEqual(notes['released_on'], '2026-10-16')
        self.assertIn('Переводы в цели', notes['notes_html'])
        self.assertNotIn('Старое', notes['notes_html'])

    def test_version_without_section_returns_empty_notes(self):
        self.write_changelog()
        notes = find_installed_release_notes(self.config, 'v0.0.13')
        self.assertEqual(notes['notes_html'], '')
        self.assertEqual(notes['released_label'], '')

    def test_unreleased_never_matches_a_release(self):
        sections = parse_changelog(CHANGELOG)
        self.assertNotIn('Unreleased', sections)
        self.assertEqual(find_installed_release_notes(self.config, 'Unreleased')['notes_html'], '')

    def test_malformed_headings_do_not_open_or_leak_into_sections(self):
        sections = parse_changelog(CHANGELOG)
        self.assertEqual(set(sections), {'0.0.12', '0.0.9'})
        self.assertNotIn('Битый', sections['0.0.12'][1])
        self.write_changelog()
        self.assertEqual(find_installed_release_notes(self.config, 'v0.0.11')['notes_html'], '')
        self.assertEqual(find_installed_release_notes(self.config, 'v0.0.10')['notes_html'], '')

    def test_missing_changelog_returns_empty_notes(self):
        self.assertEqual(find_installed_release_notes(self.config, 'v0.0.12')['notes_html'], '')

    def test_oversized_changelog_is_not_read(self):
        self.write_changelog(CHANGELOG + 'x' * (MAX_CHANGELOG_BYTES + 1))
        self.assertEqual(find_installed_release_notes(self.config, 'v0.0.12')['notes_html'], '')

    def test_untagged_commit_returns_empty_notes_without_exception(self):
        self.write_changelog()
        notes = find_installed_release_notes(self.config, None)
        self.assertEqual(notes['notes_html'], '')
        details = get_installed_release(self.config, {'label': 'коммит abc1234', 'tag': None, 'commit': None})
        self.assertEqual(details['notes_html'], '')
        self.assertEqual(details['release_url'], '')

    def test_changelog_html_injection_is_sanitized(self):
        self.write_changelog(
            '## [1.0.0] - 2026-10-01\n\n'
            '<script>alert(1)</script>\n\n'
            '- [ссылка](javascript:alert(2)) <img src=x onerror=alert(3)>\n'
        )
        html = str(find_installed_release_notes(self.config, 'v1.0.0')['notes_html'])
        self.assertNotIn('<script', html)
        self.assertNotIn('<img', html)
        self.assertNotIn('href="javascript', html)
        self.assertNotIn('<a ', html)
        self.assertIn('&lt;script&gt;', html)

    def test_changelog_symlink_outside_app_dir_is_ignored(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        target = Path(outside.name) / 'CHANGELOG.md'
        target.write_text(CHANGELOG, encoding='utf-8')
        try:
            os.symlink(target, self.directory / 'CHANGELOG.md')
        except (OSError, NotImplementedError):
            self.skipTest('Symbolic links are not available.')
        self.assertEqual(find_installed_release_notes(self.config, 'v0.0.12')['notes_html'], '')

    def test_parsed_notes_are_cached_by_path_and_mtime(self):
        path = self.write_changelog()
        with patch.object(installed_release_service, 'render_release_notes',
                          wraps=installed_release_service.render_release_notes) as render:
            find_installed_release_notes(self.config, 'v0.0.12')
            find_installed_release_notes(self.config, 'v0.0.12')
            self.assertEqual(render.call_count, 1)
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
            find_installed_release_notes(self.config, 'v0.0.12')
            self.assertEqual(render.call_count, 2)

    @patch.object(server_update_service.subprocess, 'run')
    def test_commit_details_use_git_date_and_validated_repository(self, run):
        (self.directory / '.git').mkdir()
        run.return_value = Mock(returncode=0, stdout='2026-10-15T12:16:00+00:00\n')
        details = get_commit_details(self.config, 'abc123def456')
        self.assertEqual(details['date_label'], '15 октября 2026, 12:16')
        self.assertEqual(details['url'], 'https://github.com/example-owner/officium/commit/abc123def456')
        self.assertEqual(run.call_args.args[0][-4:], ['log', '-1', '--format=%cI', 'HEAD'])

    @patch.object(server_update_service.subprocess, 'run', side_effect=OSError)
    def test_commit_details_are_optional(self, _run):
        (self.directory / '.git').mkdir()
        self.config['SERVER_UPDATE_REPOSITORY'] = 'not a repository'
        details = get_commit_details(self.config, 'abc123def456')
        self.assertEqual(details, {'date': '', 'date_label': '', 'url': ''})
        self.assertEqual(get_commit_details(self.config, '--evil')['url'], '')

    def test_last_successful_update_comes_from_status_file(self):
        status_path = Path(self.config['SERVER_UPDATE_STATUS_PATH'])
        status_path.write_text(json.dumps({
            'state': 'success', 'tag': 'v0.0.12', 'updated_at': '2026-10-15T12:20:00Z',
        }), encoding='utf-8')
        last = get_last_successful_update(self.config)
        self.assertEqual(last['tag'], 'v0.0.12')
        self.assertEqual(last['label'], '15 октября 2026, 12:20')
        status_path.write_text(json.dumps({'state': 'failed', 'tag': 'v0.0.13'}), encoding='utf-8')
        self.assertIsNone(get_last_successful_update(self.config))

    def test_database_revision_is_read_without_subprocess(self):
        engine = create_engine('sqlite://')
        self.assertEqual(get_database_revision(engine), '')
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)'))
            connection.execute(text("INSERT INTO alembic_version VALUES ('20261009_monthly')"))
        self.assertEqual(get_database_revision(engine), '20261009_monthly')

    def test_database_errors_leave_revision_empty(self):
        engine = Mock()
        from sqlalchemy.exc import OperationalError
        engine.connect.side_effect = OperationalError('SELECT', {}, Exception('down'))
        self.assertEqual(get_database_revision(engine), '')


class InstalledReleasePageTestCase(unittest.TestCase):
    def setUp(self):
        installed_release_service._cache.clear()
        self.temporary_directory = tempfile.TemporaryDirectory()
        directory = Path(self.temporary_directory.name)
        (directory / 'CHANGELOG.md').write_text(
            '## [Unreleased]\n\n## [1.1.0] - 2026-10-15\n\n- Новая возможность релиза.\n\n'
            '## [1.0.0] - 2026-10-01\n\n- Исправление установленной версии.\n',
            encoding='utf-8',
        )
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'installed-release-test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'SERVER_UPDATE_ENABLED': True,
            'SERVER_UPDATE_REPOSITORY': 'example-owner/officium',
            'SERVER_UPDATE_APP_DIR': str(directory),
            'SERVER_UPDATE_STATUS_PATH': str(directory / 'status.json'),
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            superadmin = User(telegram_id=-2001, username='root-admin', role='superadmin')
            db.session.add(superadmin)
            db.session.commit()
            self.superadmin_id = superadmin.id
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.superadmin_id)
            session['_fresh'] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        installed_release_service._cache.clear()
        self.temporary_directory.cleanup()

    @staticmethod
    def latest(tag, notes):
        return {
            'tag': tag, 'name': f'Release {tag}', 'published_at': '2026-10-15T10:00:00Z',
            'published_label': '15 октября 2026, 13:00', 'notes_html': notes, 'notes_truncated': False,
            'url': f'https://github.com/example-owner/officium/releases/tag/{tag}',
        }

    def get_page(self, current, latest=None):
        with patch('app.routes.admin.get_current_release', return_value=current), \
                patch('app.routes.admin.fetch_latest_release', return_value=latest), \
                patch('app.routes.admin.check_update_readiness', return_value={'ok': True}), \
                patch('app.routes.admin.get_update_status', return_value=None):
            response = self.client.get('/admin/server-update')
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_current_release_notes_are_not_repeated(self):
        from markupsafe import Markup
        html = self.get_page(
            {'label': 'v1.1.0', 'tag': 'v1.1.0', 'tags': ['v1.1.0'], 'commit': None},
            self.latest('v1.1.0', Markup('<ul><li>Новая возможность релиза.</li></ul>')),
        )
        self.assertEqual(html.count('Новая возможность релиза.'), 1)
        self.assertIn('data-release-notes="installed"', html)
        self.assertNotIn('data-release-notes="latest"', html)
        self.assertIn('Установлена последняя версия', html)
        self.assertIn('Успешно', html)
        self.assertIn('Опубликован:', html)
        self.assertIn('Открыть релиз на GitHub', html)
        self.assertIn('Выпущена: <time datetime="2026-10-15">15 октября 2026</time>', html)

    def test_different_versions_show_both_notes(self):
        from markupsafe import Markup
        html = self.get_page(
            {'label': 'v1.0.0', 'tag': 'v1.0.0', 'tags': ['v1.0.0'], 'commit': 'abc123def456'},
            self.latest('v1.1.0', Markup('<ul><li>Новая возможность релиза.</li></ul>')),
        )
        self.assertIn('Исправление установленной версии.', html)
        self.assertIn('Новая возможность релиза.', html)
        self.assertIn('data-release-notes="latest"', html)
        self.assertIn('href="https://github.com/example-owner/officium/commit/abc123def456"', html)
        self.assertIn('Обновить до v1.1.0', html)

    def test_disabled_update_still_shows_installed_notes(self):
        self.app.config['SERVER_UPDATE_ENABLED'] = False
        html = self.get_page({'label': 'v1.0.0', 'tag': 'v1.0.0', 'tags': ['v1.0.0'], 'commit': None})
        self.assertIn('Исправление установленной версии.', html)
        self.assertIn('Обновление отключено.', html)

    def test_missing_notes_are_explained(self):
        html = self.get_page({'label': 'коммит abc1234', 'tag': None, 'tags': [], 'commit': 'abc1234'})
        self.assertIn('Описание этой версии не найдено в CHANGELOG.md.', html)
        self.assertNotIn('Ревизия БД', html)


if __name__ == '__main__':
    unittest.main()
