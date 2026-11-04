from datetime import date, datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from app.models import Expense, MonthlyExpenseReminderLog, User
from app.services import monthly_reminders as reminders
from app.services.telegram_transport import TelegramDeliveryError, TelegramDeliveryResult
from app.utils import format_currency
from extensions import db
import test_personal_settings


class MonthlyRemindersTests(unittest.TestCase):
    setUp = test_personal_settings.PersonalSettingsTests.setUp
    tearDown = test_personal_settings.PersonalSettingsTests.tearDown
    login = test_personal_settings.PersonalSettingsTests.login

    def expense(self, *, user=None, day=31, amount='1600', group='rent', monthly=True):
        row = Expense(user_id=(user or self.user).id, amount=Decimal(amount), category='rent',
                      title='Аренда', expense_date=date(2024, 1, day), is_monthly=monthly,
                      monthly_group_id=group, monthly_anchor_day=day, generated_for_month='2024-01')
        db.session.add(row)
        db.session.commit()
        return row

    def instant(self, day, hour=7, minute=0):
        return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).replace(hour=hour, minute=minute)

    def test_short_months_and_leap_year(self):
        self.expense()
        for due in (date(2026, 4, 30), date(2026, 2, 28), date(2028, 2, 29)):
            from datetime import timedelta
            with self.subTest(due=due), patch.object(reminders.telegram, 'send_telegram_message', return_value=True) as sender:
                self.assertEqual(reminders.run(self.instant(due - timedelta(days=1))), 1)
                self.assertIn(due.strftime('%d.%m.%Y'), sender.call_args.args[1])
                self.assertEqual(reminders.run(self.instant(due - timedelta(days=1))), 0)
        self.assertEqual(MonthlyExpenseReminderLog.query.count(), 3)

    def test_month_and_year_boundaries(self):
        self.expense(day=1)
        for today, due in ((date(2026, 1, 31), '01.02.2026'), (date(2026, 12, 31), '01.01.2027')):
            with self.subTest(today=today), patch.object(reminders.telegram, 'send_telegram_message', return_value=True) as sender:
                self.assertEqual(reminders.run(self.instant(today)), 1)
                self.assertIn(due, sender.call_args.args[1])

    def test_delivery_window(self):
        self.expense()
        today = date(2026, 4, 29)
        with patch.object(reminders.telegram, 'send_telegram_message', return_value=True) as sender:
            self.assertEqual(reminders.run(self.instant(today, 6, 59)), 0)
            self.assertEqual(reminders.run(self.instant(today, 13, 1)), 0)
            self.assertEqual(reminders.run(self.instant(today, 13)), 1)
        sender.assert_called_once()

    def test_disabled_blocked_and_without_telegram(self):
        self.expense()
        self.expense(user=self.other)
        self.expense(user=self.google)
        self.user.reminder_enabled = False
        self.other.is_blocked = True
        db.session.commit()
        with patch.object(reminders.telegram, 'send_telegram_message') as sender:
            self.assertEqual(reminders.run(self.instant(date(2026, 4, 29))), 0)
            sender.assert_not_called()

    def test_timezones_at_same_instant(self):
        self.expense()
        self.expense(user=self.other)
        self.other.timezone = 'UTC'
        db.session.commit()
        with patch.object(reminders.telegram, 'send_telegram_message', return_value=True) as sender:
            self.assertEqual(reminders.run(self.instant(date(2026, 4, 29))), 1)
            self.assertEqual(sender.call_args.args[0], self.user.telegram_id)
            self.assertEqual(reminders.run(self.instant(date(2026, 4, 29), 10)), 1)
            self.assertEqual(sender.call_args.args[0], self.other.telegram_id)

    def test_summary_and_series_copies(self):
        origin = self.expense()
        copy = Expense(user_id=self.user.id, amount=Decimal('1600'), category='rent', title='Аренда',
                       expense_date=date(2024, 2, 29), monthly_group_id='rent', is_monthly=True,
                       generated_from_id=origin.id, generated_for_month='2024-02', monthly_anchor_day=31)
        db.session.add(copy)
        db.session.commit()
        self.expense(group='internet', amount='500.50')
        self.expense(group='ordinary', monthly=False)
        with patch.object(reminders.telegram, 'send_telegram_message', return_value=True) as sender:
            self.assertEqual(reminders.run(self.instant(date(2026, 4, 29))), 1)
        sender.assert_called_once()
        self.assertIn('Итого: ' + format_currency(Decimal('2100.50')), sender.call_args.args[1])
        self.assertEqual(MonthlyExpenseReminderLog.query.count(), 2)
        self.assertIn(origin.id, [row.monthly_expense_id for row in MonthlyExpenseReminderLog.query.all()])

    def test_dry_run_has_no_side_effects(self):
        self.expense()
        with patch.object(reminders.telegram, 'send_telegram_message') as sender:
            self.assertEqual(reminders.run(self.instant(date(2026, 4, 29)), dry_run=True), 1)
            sender.assert_not_called()
        self.assertEqual(MonthlyExpenseReminderLog.query.count(), 0)
        self.assertTrue(db.session.get(User, self.user.id).reminder_enabled)

    def test_errors_do_not_stop_other_users_or_write_success_log(self):
        self.expense()
        self.expense(user=self.other)
        error = TelegramDeliveryError(TelegramDeliveryResult(failure_kind='timeout'))
        with patch.object(reminders.telegram, 'send_telegram_message', side_effect=[error, True]), \
                self.assertLogs(self.app.logger, level='WARNING') as logs:
            self.assertEqual(reminders.run(self.instant(date(2026, 4, 29))), 1)
        self.assertNotIn('Аренда', str(logs.output))
        self.assertNotIn('1600', str(logs.output))
        self.assertEqual([row.user_id for row in MonthlyExpenseReminderLog.query.all()], [self.other.id])

    def test_blocked_and_missing_chat_disable_reminders(self):
        self.expense()
        for kind in ('blocked', 'chat_unavailable'):
            self.user.reminder_enabled = True
            db.session.commit()
            with self.subTest(kind=kind), patch.object(reminders.telegram, 'send_telegram_message',
                                                       side_effect=TelegramDeliveryError(TelegramDeliveryResult(failure_kind=kind))):
                self.assertEqual(reminders.run(self.instant(date(2026, 4, 29))), 0)
            db.session.expire_all()
            self.assertFalse(self.user.reminder_enabled)
            self.assertEqual(MonthlyExpenseReminderLog.query.count(), 0)

    def test_false_and_unknown_error_do_not_write_log(self):
        self.expense()
        for outcome in (False, RuntimeError('private proxy credentials')):
            with self.subTest(outcome=type(outcome)), patch.object(reminders.telegram, 'send_telegram_message',
                                                                   side_effect=outcome if isinstance(outcome, Exception) else None, return_value=False), \
                    self.assertLogs(self.app.logger, level='WARNING') as logs:
                self.assertEqual(reminders.run(self.instant(date(2026, 4, 29))), 0)
            self.assertNotIn('private proxy credentials', str(logs.output))
            self.assertEqual(MonthlyExpenseReminderLog.query.count(), 0)

    def test_profile_validation_csrf_and_owner(self):
        data = dict(section='monthly_reminders', reminder_enabled='on', reminder_time='09:30',
                    reminder_days_before='7', reminder_timezone='Asia/Vladivostok', user_id=self.other.id)
        self.assertEqual(self.client.post('/profile', data=data).status_code, 302)
        self.assertEqual((self.user.reminder_time, self.user.reminder_days_before, self.user.timezone),
                         ('09:30', 7, 'Asia/Vladivostok'))
        self.assertEqual(self.other.timezone, 'Europe/Moscow')
        self.assertIn('Напоминания о ежемесячных расходах', self.client.get('/profile').get_data(as_text=True))
        for key, value in (('reminder_time', '24:00'), ('reminder_time', '1:00'),
                           ('reminder_days_before', '8'), ('reminder_days_before', '-1'),
                           ('reminder_timezone', 'Not/AZone')):
            with self.subTest(key=key, value=value):
                self.assertEqual(self.client.post('/profile', data={**data, key: value}).status_code, 400)
                self.assertEqual(self.user.reminder_time, '09:30')
        self.app.config['WTF_CSRF_ENABLED'] = True
        self.assertEqual(self.client.post('/profile', data=data).status_code, 400)

    def test_profile_without_telegram_and_disabled_setting(self):
        data = dict(section='monthly_reminders', reminder_time='10:00', reminder_days_before='0',
                    reminder_timezone='Europe/Moscow')
        self.assertEqual(self.client.post('/profile', data=data).status_code, 302)
        self.assertFalse(self.user.reminder_enabled)
        self.login(self.google)
        self.assertEqual(self.client.post('/profile', data=data).status_code, 403)

    def test_cli_dry_run_reports_count_without_sending(self):
        with patch.object(reminders, 'run', return_value=2) as run:
            result = self.app.test_cli_runner().invoke(args=['send-monthly-expense-reminders', '--dry-run'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('Messages planned: 2', result.output)
        run.assert_called_once_with(dry_run=True)

    def test_concurrent_workers_send_one_summary(self):
        from concurrent.futures import ThreadPoolExecutor
        from pathlib import Path
        import sqlite3
        import tempfile
        import time
        from app import create_app

        self.expense()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'concurrent.sqlite'
            source = db.engine.raw_connection()
            try:
                with sqlite3.connect(path) as target:
                    source.driver_connection.backup(target)
                target.close()
            finally:
                source.close()
            app = create_app(dict(TESTING=True, SECRET_KEY='concurrency-test',
                                  SQLALCHEMY_DATABASE_URI='sqlite:///' + path.as_posix(),
                                  SQLALCHEMY_ENGINE_OPTIONS={}))

            def worker():
                with app.app_context():
                    return reminders.run(self.instant(date(2026, 4, 29)))

            def send(*args, **kwargs):
                time.sleep(0.1)
                return True

            with patch.object(reminders.telegram, 'send_telegram_message', side_effect=send) as sender, \
                    ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: worker(), range(2)))
            self.assertEqual(sorted(results), [0, 1])
            sender.assert_called_once()
            with app.app_context():
                self.assertEqual(MonthlyExpenseReminderLog.query.count(), 1)
                db.session.remove()
                db.engine.dispose()
