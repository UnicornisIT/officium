"""Bank statement import storage: placement, limits, cleanup and confirmation.

All statements here are synthetic.
"""

import errno
import io
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
import zipfile
from collections import namedtuple
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app import create_app, resolve_instance_path
from app.models import Expense, User
from app.services import import_storage_service as storage
from app.services.bank_statement_import_service import parse_bank_statement
from extensions import db

CSV_HEADER = 'Сбербанк Онлайн\nДата операции;Описание операции;Сумма операции;Категория;Карта\n'


def statement(*lines):
    return (CSV_HEADER + ''.join(line + '\n' for line in lines)).encode('cp1251')


def expense_lines(count, start=0):
    return [f'{index % 28 + 1:02d}.07.2026;SYNTH SHOP {index};-{100 + index},00;Супермаркеты;*0000'
            for index in range(start, start + count)]


DiskUsage = namedtuple('DiskUsage', 'total used free')


class InstancePathTestCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='officium-layout-'))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def make_release(self, with_shared=True):
        release = self.root / 'releases' / 'v1.0.0-0123456789ab'
        release.mkdir(parents=True)
        (release / '.officium-release-ready').write_text('0' * 40 + '\n', encoding='utf-8')
        if with_shared:
            (self.root / 'shared' / 'instance').mkdir(parents=True)
        return release

    def test_managed_release_uses_shared_instance(self):
        release = self.make_release()
        self.assertEqual(
            resolve_instance_path(str(release), environ={}),
            str(self.root / 'shared' / 'instance'),
        )

    def test_release_reached_through_current_symlink_uses_shared_instance(self):
        release = self.make_release()
        current = self.root / 'current'
        try:
            current.symlink_to(release, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks are not available')
        self.assertEqual(
            resolve_instance_path(str(current), environ={}),
            str(self.root / 'shared' / 'instance'),
        )

    def test_explicit_setting_wins(self):
        release = self.make_release()
        explicit = self.root / 'elsewhere'
        self.assertEqual(
            resolve_instance_path(str(release), environ={'OFFICIUM_INSTANCE_PATH': str(explicit)}),
            str(explicit),
        )

    def test_release_without_shared_directory_keeps_flask_default(self):
        release = self.make_release(with_shared=False)
        self.assertIsNone(resolve_instance_path(str(release), environ={}))

    def test_development_checkout_keeps_flask_default(self):
        checkout = self.root / 'checkout'
        checkout.mkdir()
        self.assertIsNone(resolve_instance_path(str(checkout), environ={}))


class ImportStorageAppTestCase(unittest.TestCase):
    extra_config = {}

    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix='officium-import-'))
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)
        self.storage_dir = self.workdir / 'bank_imports'
        self.database = self.workdir / 'officium.sqlite'
        self.app = self.make_app()
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            self.user_id = self.add_user(9001, 'import_one')
            self.other_user_id = self.add_user(9002, 'import_two')

    def make_app(self, **overrides):
        config = {
            'TESTING': True,
            'SECRET_KEY': 'import-storage-test-secret-0123456789',
            'SQLALCHEMY_DATABASE_URI': f'sqlite:///{self.database.as_posix()}',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'IMPORT_STORAGE_DIR': str(self.storage_dir),
            'IMPORT_DISK_RESERVE_BYTES': 0,
            'IMPORT_PARSE_WAIT_SECONDS': 0.2,
        }
        config.update(self.extra_config)
        config.update(overrides)
        return create_app(config)

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def add_user(self, telegram_id, username):
        user = User(telegram_id=telegram_id, username=username, first_name='Synthetic', role='user')
        db.session.add(user)
        db.session.commit()
        return user.id

    def login(self, client, user_id):
        with client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True

    def upload(self, client, data, filename='synthetic.csv'):
        return client.post(
            '/expenses/import',
            data={'statement_file': (io.BytesIO(data), filename)},
            content_type='multipart/form-data',
        )

    def preview_token(self, response):
        match = re.search(r'name="import_token" value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match, response.get_data(as_text=True)[:500])
        return match.group(1)

    def confirm(self, client, token, rows=1, **fields):
        form = {'import_token': token, 'row_count': str(rows)}
        for index in range(rows):
            form.update({
                f'action_{index}': 'create',
                f'expense_date_{index}': '2026-07-01',
                f'title_{index}': f'SYNTH SHOP {index}',
                f'category_{index}': 'products',
                f'amount_{index}': f'{100 + index}.00',
                f'payment_method_{index}': 'card',
                f'comment_{index}': 'Импорт выписки: Сбер',
            })
        form.update(fields)
        return client.post('/expenses/import/confirm', data=form)

    def stored_files(self):
        if not self.storage_dir.exists():
            return []
        return sorted(path.name for path in self.storage_dir.iterdir())

    def expense_count(self, user_id=None):
        with self.app.app_context():
            return Expense.query.filter_by(user_id=user_id or self.user_id).count()


class ImportFlowTestCase(ImportStorageAppTestCase):
    def test_preview_is_stored_outside_the_release_and_confirmed(self):
        self.login(self.client, self.user_id)
        response = self.upload(self.client, statement(*expense_lines(1)))
        self.assertEqual(response.status_code, 200)
        token = self.preview_token(response)
        self.assertEqual(self.stored_files(), [f'u{self.user_id}-{token}.json'])
        self.assertFalse((Path(self.app.instance_path) / 'bank_imports' / f'u{self.user_id}-{token}.json').exists())

        response = self.confirm(self.client, token)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.expense_count(), 1)
        self.assertEqual(self.stored_files(), [])

    def test_preview_file_holds_no_statement_content(self):
        self.login(self.client, self.user_id)
        # A timestamp may contain the same digits as an amount. Validate the
        # complete metadata schema instead of searching raw JSON for "100".
        now = datetime.now(timezone.utc).replace(microsecond=861005)
        with patch.object(storage, 'utc_now', return_value=now):
            token = self.preview_token(self.upload(self.client, statement(*expense_lines(3))))
        raw = (self.storage_dir / f'u{self.user_id}-{token}.json').read_text(encoding='utf-8')
        self.assertNotIn('SYNTH', raw)
        payload = json.loads(raw)
        self.assertEqual(set(payload), {'version', 'user_id', 'created_at', 'row_count', 'monthly_match_ids'})
        self.assertEqual(payload['version'], storage.PAYLOAD_VERSION)
        self.assertEqual(payload['created_at'], now.replace(tzinfo=None).isoformat())
        self.assertEqual(payload['monthly_match_ids'], [None, None, None])
        self.assertEqual(payload['row_count'], 3)
        self.assertEqual(payload['user_id'], self.user_id)
        if os.name == 'posix':
            self.assertEqual(os.stat(self.storage_dir / f'u{self.user_id}-{token}.json').st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(self.storage_dir).st_mode & 0o777, 0o700)

    def test_largest_accepted_statement_imports_all_rows(self):
        self.login(self.client, self.user_id)
        rows = self.app.config['IMPORT_MAX_ROWS']
        response = self.upload(self.client, statement(*expense_lines(rows)))
        self.assertEqual(response.status_code, 200)
        token = self.preview_token(response)
        size = (self.storage_dir / f'u{self.user_id}-{token}.json').stat().st_size
        self.assertLess(size, 16 * 1024)
        response = self.confirm(self.client, token, rows=rows)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.expense_count(), rows)

    def test_statement_with_too_many_expenses_is_rejected_without_storage(self):
        self.login(self.client, self.user_id)
        rows = self.app.config['IMPORT_MAX_ROWS'] + 1
        response = self.upload(self.client, statement(*expense_lines(rows)))
        self.assertEqual(response.status_code, 400)
        self.assertIn(f'больше {rows - 1} расходов', response.get_data(as_text=True))
        self.assertEqual(self.stored_files(), [])

    def test_request_over_upload_limit_answers_413(self):
        app = self.make_app(MAX_CONTENT_LENGTH=2048)
        client = app.test_client()
        self.login(client, self.user_id)
        response = self.upload(client, statement(*expense_lines(200)))
        self.assertEqual(response.status_code, 413)
        self.assertIn('Файл слишком большой', response.get_data(as_text=True))
        self.assertEqual(self.stored_files(), [])

    def test_restart_between_preview_and_confirmation(self):
        self.login(self.client, self.user_id)
        token = self.preview_token(self.upload(self.client, statement(*expense_lines(1))))
        restarted = self.make_app()
        client = restarted.test_client()
        self.login(client, self.user_id)
        self.assertEqual(self.confirm(client, token).status_code, 302)
        self.assertEqual(self.expense_count(), 1)

    def test_second_confirmation_does_not_duplicate_expenses(self):
        self.login(self.client, self.user_id)
        token = self.preview_token(self.upload(self.client, statement(*expense_lines(2))))
        self.assertEqual(self.confirm(self.client, token, rows=2).status_code, 302)
        second = self.confirm(self.client, token, rows=2)
        self.assertEqual(second.status_code, 410)
        self.assertIn('Загрузите выписку еще раз', second.get_data(as_text=True))
        self.assertEqual(self.expense_count(), 2)

    def test_confirmation_in_progress_blocks_a_parallel_one(self):
        self.login(self.client, self.user_id)
        token = self.preview_token(self.upload(self.client, statement(*expense_lines(1))))
        with self.app.test_request_context():
            claim = storage.claimed_preview(self.user_id, token)
            with claim:
                parallel = self.confirm(self.client, token)
                self.assertEqual(parallel.status_code, 409)
                self.assertIn('уже подтверждается', parallel.get_data(as_text=True))
                # Cleanup running meanwhile keeps the fresh claim.
                self.assertEqual(storage.cleanup_expired(self.app).removed, 0)
                claim.complete()
        self.assertEqual(self.expense_count(), 0)
        self.assertEqual(self.stored_files(), [])

    def test_failed_confirmation_keeps_data_and_preview(self):
        with self.app.app_context():
            db.session.add(Expense(
                user_id=self.user_id, amount=Decimal('5.00'), category='products',
                title='Existing synthetic', expense_date=__import__('datetime').date(2026, 6, 1),
            ))
            db.session.commit()
        self.login(self.client, self.user_id)
        token = self.preview_token(self.upload(self.client, statement(*expense_lines(2))))
        response = self.confirm(self.client, token, rows=2, category_1='not-a-category')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.expense_count(), 1)
        self.assertEqual(self.stored_files(), [f'u{self.user_id}-{token}.json'])

        self.assertEqual(self.confirm(self.client, token, rows=2).status_code, 302)
        self.assertEqual(self.expense_count(), 3)

    def test_database_failure_rolls_back_and_keeps_preview(self):
        self.login(self.client, self.user_id)
        token = self.preview_token(self.upload(self.client, statement(*expense_lines(2))))
        with patch('extensions.db.session.commit', side_effect=RuntimeError('synthetic database outage')), \
                self.assertLogs(self.app.logger, level='ERROR') as logs:
            response = self.confirm(self.client, token, rows=2)
        self.assertEqual(response.status_code, 500)
        self.assertIn('Данные не были сохранены', response.get_data(as_text=True))
        self.assertNotIn('synthetic database outage', '\n'.join(logs.output))
        self.assertEqual(self.expense_count(), 0)
        self.assertEqual(self.stored_files(), [f'u{self.user_id}-{token}.json'])

    def test_users_cannot_confirm_each_others_previews(self):
        other = self.app.test_client()
        self.login(self.client, self.user_id)
        self.login(other, self.other_user_id)
        token_one = self.preview_token(self.upload(self.client, statement(*expense_lines(1))))
        token_two = self.preview_token(self.upload(other, statement(*expense_lines(1, start=50))))

        stolen = self.confirm(other, token_one)
        self.assertEqual(stolen.status_code, 410)
        self.assertEqual(self.expense_count(self.other_user_id), 0)

        self.assertEqual(self.confirm(self.client, token_one).status_code, 302)
        self.assertEqual(self.confirm(other, token_two, title_0='SYNTH SHOP 50').status_code, 302)
        self.assertEqual(self.expense_count(self.user_id), 1)
        self.assertEqual(self.expense_count(self.other_user_id), 1)

    def test_parallel_previews_of_several_users(self):
        results = {}

        def run(user_id, start):
            client = self.app.test_client()
            self.login(client, user_id)
            response = self.upload(client, statement(*expense_lines(5, start=start)))
            results[user_id] = (response.status_code, client, self.preview_token(response))

        app = self.make_app(IMPORT_PARSE_WAIT_SECONDS=10)
        self.app = app
        threads = [threading.Thread(target=run, args=(user_id, start))
                   for user_id, start in ((self.user_id, 0), (self.other_user_id, 100))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual({code for code, _client, _token in results.values()}, {200})
        self.assertEqual(len(self.stored_files()), 2)

    def test_new_upload_replaces_the_oldest_unconfirmed_previews(self):
        app = self.make_app(IMPORT_MAX_PENDING_PER_USER=2)
        client = app.test_client()
        self.login(client, self.user_id)
        tokens = []
        for _attempt in range(3):
            tokens.append(self.preview_token(self.upload(client, statement(*expense_lines(1)))))
            time.sleep(0.02)
        self.assertEqual(len(self.stored_files()), 2)
        self.assertNotIn(f'u{self.user_id}-{tokens[0]}.json', self.stored_files())
        self.assertEqual(self.confirm(client, tokens[0]).status_code, 410)
        self.assertEqual(self.confirm(client, tokens[2]).status_code, 302)

    def test_legacy_preview_file_is_still_confirmed(self):
        self.storage_dir.mkdir(parents=True)
        token = 'L' * 32
        (self.storage_dir / f'{token}.json').write_text(json.dumps({
            'user_id': self.user_id,
            'created_at': __import__('datetime').datetime.utcnow().isoformat(),
            'bank': 'sber',
            'rows': [{'title': 'SYNTH SHOP 0', 'monthly_match_id': None}],
        }), encoding='utf-8')
        self.login(self.client, self.user_id)
        self.assertEqual(self.confirm(self.client, token).status_code, 302)
        self.assertEqual(self.expense_count(), 1)
        self.assertEqual(self.stored_files(), [])


class ImportLimitsTestCase(ImportStorageAppTestCase):
    def test_low_free_space_refuses_the_preview(self):
        self.login(self.client, self.user_id)
        app = self.make_app(IMPORT_DISK_RESERVE_BYTES=512 * 1024 * 1024)
        client = app.test_client()
        self.login(client, self.user_id)
        with patch('shutil.disk_usage', return_value=DiskUsage(10 * 2**30, 10 * 2**30 - 300 * 2**20, 300 * 2**20)):
            response = self.upload(client, statement(*expense_lines(1)))
        self.assertEqual(response.status_code, 507)
        self.assertIn('мало свободного места', response.get_data(as_text=True))
        self.assertEqual([name for name in self.stored_files() if not name.startswith('.')], [])

    def test_storage_quota_refuses_the_preview(self):
        app = self.make_app(IMPORT_STORAGE_MAX_FILES=1)
        first, second = app.test_client(), app.test_client()
        self.login(first, self.user_id)
        self.login(second, self.other_user_id)
        self.assertEqual(self.upload(first, statement(*expense_lines(1))).status_code, 200)
        response = self.upload(second, statement(*expense_lines(1)))
        self.assertEqual(response.status_code, 507)
        self.assertIn('хранилище импорта заполнено', response.get_data(as_text=True))
        self.assertEqual(len(self.stored_files()), 1)

    def test_disk_full_during_write_leaves_no_partial_file(self):
        self.login(self.client, self.user_id)
        with patch('app.services.import_storage_service.os.write',
                   side_effect=OSError(errno.ENOSPC, 'No space left on device')), \
                self.assertLogs(self.app.logger, level='ERROR') as logs:
            response = self.upload(self.client, statement(*expense_lines(1)))
        self.assertEqual(response.status_code, 507)
        self.assertIn('закончилось место', response.get_data(as_text=True))
        self.assertEqual(self.stored_files(), [])
        self.assertIn('ENOSPC', '\n'.join(logs.output))

    def test_read_only_storage_answers_503_not_500(self):
        self.login(self.client, self.user_id)
        with patch('app.services.import_storage_service.os.makedirs',
                   side_effect=OSError(errno.EROFS, 'Read-only file system')), \
                self.assertLogs(self.app.logger, level='ERROR'):
            response = self.upload(self.client, statement(*expense_lines(1)))
        self.assertEqual(response.status_code, 503)
        self.assertIn('недоступно для записи', response.get_data(as_text=True))

    def test_permission_denied_on_read_answers_503(self):
        self.login(self.client, self.user_id)
        token = self.preview_token(self.upload(self.client, statement(*expense_lines(1))))
        real_open = open

        def denied(path, *args, **kwargs):
            if str(path).endswith('.claimed'):
                raise OSError(errno.EACCES, 'Permission denied')
            return real_open(path, *args, **kwargs)

        with patch('builtins.open', denied), self.assertLogs(self.app.logger, level='ERROR'):
            response = self.confirm(self.client, token)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.expense_count(), 0)
        self.assertEqual(self.stored_files(), [f'u{self.user_id}-{token}.json'])

    def test_busy_parser_slot_answers_503(self):
        self.login(self.client, self.user_id)
        with self.app.app_context():
            slot = storage.parse_slot(self.app)
            with slot:
                response = self.upload(self.client, statement(*expense_lines(1)))
        self.assertEqual(response.status_code, 503)
        self.assertIn('другую выписку', response.get_data(as_text=True))

    def test_unexpected_parser_failure_is_logged_without_content(self):
        self.login(self.client, self.user_id)
        with patch('app.routes.expenses.parse_bank_statement',
                   side_effect=RuntimeError('SYNTH SHOP 4242 card *0000 amount 999')), \
                self.assertLogs(self.app.logger, level='ERROR') as logs:
            response = self.upload(self.client, statement(*expense_lines(1)))
        self.assertEqual(response.status_code, 500)
        output = '\n'.join(logs.output)
        self.assertIn('RuntimeError', output)
        self.assertIn('Traceback', output)
        self.assertNotIn('SYNTH SHOP 4242', output)
        self.assertNotIn('*0000', output)


class ImportCleanupTestCase(ImportStorageAppTestCase):
    def write(self, name, age_seconds, content=b'{}'):
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        path = self.storage_dir / name
        path.write_bytes(content)
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    def test_cleanup_removes_only_expired_managed_files(self):
        ttl = self.app.config['IMPORT_PREVIEW_TTL_MINUTES'] * 60
        token = 'T' * 32
        expired = [
            self.write(f'u1-{token}.json', ttl + 60),
            self.write(f'u1-{"C" * 32}.json.claimed', ttl + 60),
            self.write(f'{"O" * 32}.json', ttl + 60),
            self.write('.tmp-abandoned01', storage.TEMPORARY_FILE_TTL_SECONDS + 60),
        ]
        kept = [
            self.write(f'u1-{"F" * 32}.json', 60),
            self.write(f'u1-{"A" * 32}.json.claimed', 60),
            self.write('.tmp-inprogress01', 60),
            self.write('notes.txt', ttl * 10),
            self.write('database.sqlite', ttl * 10),
        ]
        (self.storage_dir / f'u1-{"D" * 32}.json').mkdir()
        stats = storage.cleanup_expired(self.app)
        self.assertEqual(stats.removed, len(expired))
        for path in expired:
            self.assertFalse(path.exists(), path.name)
        for path in kept:
            self.assertTrue(path.exists(), path.name)
        self.assertTrue((self.storage_dir / f'u1-{"D" * 32}.json').is_dir())

    def test_cleanup_does_not_follow_symlinks(self):
        outside = self.workdir / 'outside.json'
        outside.write_text('{}', encoding='utf-8')
        stamp = time.time() - 10**6
        os.utime(outside, (stamp, stamp))
        self.storage_dir.mkdir(parents=True)
        link = self.storage_dir / f'u1-{"S" * 32}.json'
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks are not available')
        storage.cleanup_expired(self.app)
        self.assertTrue(outside.exists())

    def test_cleanup_command_runs_without_new_imports(self):
        ttl = self.app.config['IMPORT_PREVIEW_TTL_MINUTES'] * 60
        old = self.write(f'u1-{"T" * 32}.json', ttl + 60)
        result = self.app.test_cli_runner().invoke(args=['import-cleanup'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('removed 1 files', result.output)
        self.assertFalse(old.exists())

    def test_cleanup_on_missing_directory_is_a_no_op(self):
        stats = storage.cleanup_expired(self.app)
        self.assertEqual((stats.removed, stats.errors), (0, 0))

    def test_expired_preview_is_refused_and_removed(self):
        self.login(self.client, self.user_id)
        token = self.preview_token(self.upload(self.client, statement(*expense_lines(1))))
        path = self.storage_dir / f'u{self.user_id}-{token}.json'
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['created_at'] = '2020-01-01T00:00:00'
        path.write_text(json.dumps(payload), encoding='utf-8')
        response = self.confirm(self.client, token)
        self.assertEqual(response.status_code, 410)
        self.assertIn('устарел', response.get_data(as_text=True))
        self.assertEqual(self.stored_files(), [])
        self.assertEqual(self.expense_count(), 0)


class StorageCheckCommandTestCase(ImportStorageAppTestCase):
    def run_check(self, free_bytes):
        with patch('shutil.disk_usage', return_value=DiskUsage(10 * 2**30, 10 * 2**30 - free_bytes, free_bytes)):
            return self.app.test_cli_runner().invoke(args=['storage-check'])

    def test_levels(self):
        ok = self.run_check(4 * 2**30)
        self.assertEqual(ok.exit_code, 0, ok.output)
        self.assertIn('Disk ok', ok.output)
        warning = self.run_check(1 * 2**30)
        self.assertEqual(warning.exit_code, 0, warning.output)
        self.assertIn('Disk warning', warning.output)
        critical = self.run_check(500 * 2**20)
        self.assertEqual(critical.exit_code, 2, critical.output)
        self.assertIn('Disk critical', critical.output)


class StreamingParserTestCase(unittest.TestCase):
    def xlsx(self, rows, shared=False):
        ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
        strings = []
        xml_rows = []
        for row_index, values in enumerate(rows, start=1):
            cells = []
            for col_index, value in enumerate(values):
                ref = f'{"ABCD"[col_index]}{row_index}'
                if shared:
                    strings.append(value)
                    cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
                else:
                    cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>')
            xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                'xl/worksheets/sheet1.xml',
                f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{ns}"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>',
            )
            if shared:
                items = ''.join(f'<si><t>{value}</t></si>' for value in strings)
                archive.writestr('xl/sharedStrings.xml', f'<sst xmlns="{ns}">{items}</sst>')
        return buffer.getvalue()

    def rows(self, count):
        header = ['Дата операции', 'Описание операции', 'Сумма операции', 'Категория']
        return [header] + [[f'{i % 28 + 1:02d}.07.2026', f'SYNTH SHOP {i}', f'-{100 + i},00', 'Покупки']
                           for i in range(count)]

    def test_xlsx_with_shared_strings_matches_inline_strings(self):
        inline = parse_bank_statement(self.xlsx(self.rows(30)), 'a.xlsx')
        shared = parse_bank_statement(self.xlsx(self.rows(30), shared=True), 'a.xlsx')
        self.assertEqual([row.to_dict() for row in inline.rows], [row.to_dict() for row in shared.rows])
        self.assertEqual(len(inline.rows), 30)
        self.assertEqual(inline.rows[-1].row_number, 31)

    def test_expense_limit_stops_early(self):
        with self.assertRaisesRegex(ValueError, 'больше 10 расходов'):
            parse_bank_statement(self.xlsx(self.rows(50)), 'a.xlsx', max_rows=10)
        with self.assertRaisesRegex(ValueError, 'больше 10 расходов'):
            parse_bank_statement(statement(*expense_lines(50)), 'a.csv', max_rows=10)

    def test_source_row_limit(self):
        with self.assertRaisesRegex(ValueError, 'больше 20 строк'):
            parse_bank_statement(statement(*expense_lines(50)), 'a.csv', max_source_rows=20)
        result = parse_bank_statement(statement(*expense_lines(18)), 'a.csv', max_source_rows=20)
        self.assertEqual(len(result.rows), 18)

    def test_corrupt_xlsx_is_a_user_error(self):
        broken = bytearray(self.xlsx(self.rows(5)))
        with self.assertRaises(ValueError):
            parse_bank_statement(bytes(broken[:len(broken) // 2]), 'a.xlsx')

    def test_csv_with_quoted_multiline_field(self):
        data = (CSV_HEADER + '01.07.2026;"SYNTH\nSHOP";-100,00;Супермаркеты;*0000\n').encode('cp1251')
        result = parse_bank_statement(data, 'a.csv')
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0].amount, Decimal('100.00'))


if __name__ == '__main__':
    logging.basicConfig()
    unittest.main()
