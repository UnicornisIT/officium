import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from flask import g

from app import create_app
from app.models import ActivityLog, AppSetting, UpdateNotificationDelivery, User
from app.services import update_notifier as notifier
from app.utils import set_setting
from extensions import db


class UpdateNotifierTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.app = create_app(dict(TESTING=True, ENVIRONMENT='testing', SECRET_KEY='update-notification-test',
            SQLALCHEMY_DATABASE_URI='sqlite:///' + str(Path(self.temporary.name) / 'test.sqlite'),
            SQLALCHEMY_ENGINE_OPTIONS={}, WTF_CSRF_ENABLED=False,
            TELEGRAM_BOT_TOKEN='private-test-token', SERVER_UPDATE_ENABLED=False,
            SERVER_UPDATE_STATUS_PATH=str(Path(self.temporary.name) / 'status.json')))
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.root = User(telegram_id=101, role='superadmin')
        self.admin = User(telegram_id=102, role='admin')
        self.member = User(telegram_id=103, role='user')
        self.blocked = User(telegram_id=104, role='superadmin', is_blocked=True)
        self.local = User(telegram_id=-105, role='superadmin')
        db.session.add_all([self.root, self.admin, self.member, self.blocked, self.local])
        db.session.commit()
        self.client = self.app.test_client()
        self.login(self.root)
        self.now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        self.http_response = Mock()
        self.http_response.raise_for_status.return_value = None
        self.http_response.json.return_value = dict(tag_name='v1.2.3', name='<b>Release & fixes</b>', body='PRIVATE BODY')
        self.github = patch('app.services.server_update_service.requests.get', return_value=self.http_response)
        self.get = self.github.start()
        response = Mock(status_code=200)
        response.json.return_value = {'ok': True, 'result': {'message_id': 1}}
        self.telegram = patch('app.services.telegram_transport.requests.Session.post', return_value=response)
        self.post = self.telegram.start()
        self.addCleanup(self.github.stop)
        self.addCleanup(self.telegram.stop)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()
        self.temporary.cleanup()

    def login(self, user):
        g.pop('_login_user', None)
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user.id)
            session['_fresh'] = True

    def enable(self, **values):
        set_setting('update_notifications_enabled', 'true', commit=False)
        for key, value in values.items():
            set_setting(key, value, commit=False)
        db.session.commit()

    def form(self, **overrides):
        values = {key: notifier.DEFAULTS[key] for key in notifier.EDITABLE}
        values.update(update_notifications_enabled='true', **overrides)
        return values

    def test_new_release_html_and_persistent_dedup(self):
        self.enable()
        result = notifier.notify_updates(now=self.now)
        self.assertEqual(result['status'], 'sent')
        self.assertEqual(notifier.load_settings()['update_last_notified_release'], 'v1.2.3')
        payload = self.post.call_args.kwargs['json']
        self.assertEqual(payload['chat_id'], self.root.telegram_id)
        self.assertEqual(payload['parse_mode'], 'HTML')
        self.assertIn('&lt;b&gt;Release &amp; fixes&lt;/b&gt;', payload['text'])
        self.assertNotIn('PRIVATE BODY', payload['text'])
        self.assertEqual(payload['reply_markup']['inline_keyboard'][0][0]['url'],
                         'https://github.com/UnicornisIT/officium/releases/tag/v1.2.3')
        self.assertIn('/repos/UnicornisIT/officium/releases/latest', self.get.call_args.args[0])
        db.session.remove()
        self.assertEqual(notifier.notify_updates(force=True, now=self.now)['status'], 'no_change')
        self.post.assert_called_once()

    def test_disabled_interval_force_and_dynamic_settings(self):
        self.assertEqual(notifier.notify_updates(now=self.now)['status'], 'disabled')
        self.assertEqual(AppSetting.query.count(), 0)
        self.get.assert_not_called()
        self.post.assert_not_called()
        self.enable()
        notifier.notify_updates(now=self.now)
        self.assertEqual(notifier.notify_updates(now=self.now + timedelta(minutes=59))['status'], 'interval')
        self.assertEqual(self.get.call_count, 1)
        notifier.notify_updates(force=True, now=self.now + timedelta(minutes=1))
        self.assertEqual(self.get.call_count, 2)
        set_setting('update_check_interval_minutes', '15')
        notifier.notify_updates(now=self.now + timedelta(minutes=16))
        self.assertEqual(self.get.call_count, 3)

    def test_dry_run_cli_has_no_writes_or_sends(self):
        self.enable()
        before = [(row.key, row.value) for row in AppSetting.query.order_by(AppSetting.key)]
        result = self.app.test_cli_runner().invoke(args=['notify-updates', '--dry-run', '--force'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.output)['status'], 'would_send')
        self.assertEqual(before, [(row.key, row.value) for row in AppSetting.query.order_by(AppSetting.key)])
        self.assertEqual(UpdateNotificationDelivery.query.count(), 0)
        self.post.assert_not_called()

    def test_github_error_is_private_and_does_not_advance_tag(self):
        self.enable()
        self.get.side_effect = requests.ConnectionError('private-test-token')
        with self.assertLogs(self.app.logger, level='WARNING') as logs:
            result = notifier.notify_updates(now=self.now)
        self.assertEqual(result['status'], 'github_error')
        self.assertEqual(notifier.load_settings()['update_last_check_status'], 'github_error')
        self.assertEqual(notifier.load_settings()['update_last_notified_release'], '')
        self.assertNotIn('private-test-token', str(logs.output))
        self.post.assert_not_called()

    def test_partial_failure_retries_only_rejected_recipient(self):
        self.enable(update_notify_recipients='admins')
        accepted = Mock(status_code=200)
        accepted.json.return_value = {'ok': True}
        rejected = Mock(status_code=403)
        rejected.json.return_value = {'ok': False, 'error_code': 403, 'description': 'private-test-token'}
        self.post.side_effect = [accepted, rejected]
        self.assertEqual(notifier.notify_updates(now=self.now)['status'], 'telegram_failed')
        self.assertEqual(notifier.load_settings()['update_last_notified_release'], '')
        self.post.side_effect = None
        self.post.return_value = accepted
        self.assertEqual(notifier.notify_updates(force=True, now=self.now)['status'], 'sent')
        self.assertEqual([call.kwargs['json']['chat_id'] for call in self.post.call_args_list], [101, 102, 102])

    def test_timeout_is_unknown_and_never_blindly_retried(self):
        self.enable()
        self.post.side_effect = requests.Timeout('private-test-token')
        with self.assertLogs(self.app.logger, level='WARNING') as logs:
            self.assertEqual(notifier.notify_updates(now=self.now)['status'], 'delivery_unknown')
        self.assertNotIn('private-test-token', str(logs.output))
        self.assertEqual(notifier.notify_updates(force=True, now=self.now)['status'], 'delivery_unknown')
        self.post.assert_called_once()
        self.assertEqual(UpdateNotificationDelivery.query.one().status, 'unknown')
        self.assertEqual(notifier.load_settings()['update_last_notified_release'], '')

    def test_reservation_survives_crash_and_blocks_other_worker(self):
        self.enable()
        db.session.add(UpdateNotificationDelivery(event_key='release:v1.2.3', user_id=self.root.id,
                                                 status='reserved', attempted_at=self.now))
        db.session.commit()
        db.session.remove()
        self.assertEqual(notifier.notify_updates(now=self.now)['status'], 'delivery_unknown')
        self.post.assert_not_called()
        self.assertEqual(UpdateNotificationDelivery.query.count(), 1)

    def test_shared_lease_and_expiry(self):
        self.enable()
        lease = notifier._acquire('update_notifications_lease', self.now)
        db.session.remove()
        self.assertEqual(notifier.notify_updates(now=self.now)['status'], 'busy')
        self.get.assert_not_called()
        self.assertIsNone(notifier._acquire('update_notifications_lease', self.now))
        newer = notifier._acquire('update_notifications_lease', self.now + timedelta(minutes=11))
        self.assertIsNotNone(newer)
        notifier._release('update_notifications_lease', lease)
        self.assertIsNone(notifier._acquire('update_notifications_lease', self.now + timedelta(minutes=11)))

    def test_concurrent_workers_send_only_once(self):
        self.enable()
        db.session.remove()
        entered, resume = threading.Event(), threading.Event()
        outcomes = []

        def github_response(*args, **kwargs):
            entered.set()
            if not resume.wait(15):
                raise requests.Timeout('test worker did not resume')
            return self.http_response

        def first_worker():
            with self.app.app_context():
                outcomes.append(notifier.notify_updates(now=self.now))

        self.get.side_effect = github_response
        worker = threading.Thread(target=first_worker)
        worker.start()
        try:
            self.assertTrue(entered.wait(10))
            self.assertEqual(notifier.notify_updates(now=self.now)['status'], 'busy')
            db.session.remove()
        finally:
            resume.set()
            worker.join(15)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcomes[0]['status'], 'sent')
        self.post.assert_called_once()
        self.assertEqual(UpdateNotificationDelivery.query.count(), 1)

    def test_install_result_outside_release_interval_and_dedup(self):
        self.enable()
        notifier.notify_updates(now=self.now)
        status_path = Path(self.app.config['SERVER_UPDATE_STATUS_PATH'])
        status_path.write_text(json.dumps(dict(state='success', tag='v1.2.3', operation_id='upd-test1',
            message='private-test-token')), encoding='utf-8')
        notifier.notify_updates(now=self.now + timedelta(minutes=1))
        self.assertEqual(self.post.call_args.kwargs['json']['text'], '✅ Сервер обновлён до v1.2.3')
        self.assertEqual(self.get.call_count, 1)
        notifier.notify_updates(now=self.now + timedelta(minutes=2))
        self.assertEqual(self.post.call_count, 2)
        status_path.write_text(json.dumps(dict(state='failed', tag='v1.2.4', operation_id='upd-test2')),
                               encoding='utf-8')
        notifier.notify_updates(now=self.now + timedelta(minutes=3))
        self.assertEqual(self.post.call_args.kwargs['json']['text'], '❌ Обновление до v1.2.4 не удалось')

    def test_recipient_filter_no_recipients_and_individual_switches(self):
        self.enable(update_notify_recipients='admins')
        notifier.notify_updates(now=self.now)
        self.assertEqual([call.kwargs['json']['chat_id'] for call in self.post.call_args_list], [101, 102])
        self.post.reset_mock()
        self.enable(update_notify_new_release='false', update_notify_install_result='false')
        notifier.notify_updates(force=True, now=self.now)
        self.post.assert_not_called()
        self.enable(update_notify_new_release='true', update_notify_recipients='superadmin')
        self.root.is_blocked = True
        db.session.commit()
        self.http_response.json.return_value['tag_name'] = 'v1.2.4'
        self.assertEqual(notifier.notify_updates(force=True, now=self.now)['status'], 'no_recipients')

    def test_superadmin_settings_audit_and_readonly_fields(self):
        response = self.client.post('/admin/settings/update-notifications', data=self.form(
            update_check_interval_minutes='15', update_last_notified_release='forged'))
        self.assertEqual(response.status_code, 302)
        values = notifier.load_settings()
        self.assertEqual(values['update_check_interval_minutes'], '15')
        self.assertEqual(values['update_last_notified_release'], '')
        logs = ActivityLog.query.filter_by(entity_type='setting').all()
        self.assertEqual(len(logs), 2)
        self.assertTrue(all(log.user_id == self.root.id for log in logs))
        self.assertIn('false → true', str([log.description for log in logs]))
        self.assertNotIn('private-test-token', str([log.description for log in logs]))
        html = self.client.get('/admin/settings').get_data(as_text=True)
        self.assertIn('Уведомления об обновлениях', html)
        self.assertIn('Отправить тестовое сообщение', html)
        self.assertNotIn('private-test-token', html)
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_action_permissions_and_csrf(self):
        paths = ['/admin/settings/update-notifications', '/admin/settings/update-notifications/test',
                 '/admin/settings/update-notifications/check']
        for user in (self.member, self.admin):
            self.login(user)
            for path in paths:
                self.assertEqual(self.client.post(path, data=self.form()).status_code, 403)
        self.login(self.root)
        self.app.config['WTF_CSRF_ENABLED'] = True
        for path in paths:
            self.assertEqual(self.client.post(path, data=self.form()).status_code, 400)
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_invalid_notification_settings_do_not_save_application_settings(self):
        set_setting('app_name', 'Before')
        response = self.client.post('/admin/settings', data=self.form(
            app_name='After', debt_limit_per_user='37', payment_warning_days='7',
            urgent_payment_days='3', impersonation_timeout_minutes='15',
            update_check_interval_minutes='14',
        ))

        self.assertIn('Интервал проверки должен быть целым числом', response.get_data(as_text=True))
        self.assertEqual(AppSetting.query.filter_by(key='app_name').one().value, 'Before')
        self.assertEqual(notifier.load_settings()['update_notifications_enabled'], 'false')
        self.assertEqual(ActivityLog.query.filter_by(entity_type='setting').count(), 0)

    def test_server_validation_and_missing_token(self):
        for value in ('14', '1441', '1.5', 'oops', '-15', '999999999999999999999'):
            with self.subTest(interval=value):
                self.assertEqual(self.client.post('/admin/settings/update-notifications',
                    data=self.form(update_check_interval_minutes=value)).status_code, 400)
        for overrides in ({'update_notify_recipients': 'everyone'}, {'update_notify_new_release': 'garbage'}):
            self.assertEqual(self.client.post('/admin/settings/update-notifications',
                data=self.form(**overrides)).status_code, 400)
        self.app.config['TELEGRAM_BOT_TOKEN'] = ''
        self.assertEqual(self.client.post('/admin/settings/update-notifications', data=self.form()).status_code, 400)
        self.assertEqual(notifier.load_settings()['update_notifications_enabled'], 'false')
        self.assertIn('Токен Telegram-бота не настроен', self.client.get('/admin/settings').get_data(as_text=True))
        self.post.assert_not_called()

    def test_admin_test_is_owner_only_rate_limited_and_check_is_forced(self):
        self.client.post('/admin/settings/update-notifications/test', data={'user_id': self.admin.id})
        self.assertEqual(self.post.call_args.kwargs['json']['chat_id'], self.root.telegram_id)
        self.client.post('/admin/settings/update-notifications/test')
        self.assertEqual(self.post.call_count, 1)
        self.enable()
        notifier.notify_updates(now=self.now)
        with patch.object(notifier, 'notify_updates', return_value={'status': 'no_change'}) as check:
            self.assertEqual(self.client.post('/admin/settings/update-notifications/check').status_code, 302)
            check.assert_called_once_with(force=True)


if __name__ == '__main__':
    unittest.main()
