"""Every setting shown in the admin form must change real behaviour."""

import re
import unittest
from datetime import date, timedelta

from app import create_app
from app.models import AppSetting, Debt, User
from app.routes.admin import SETTINGS_FIELDS
from app.services.telegram_bot_service import send_debt_reminders
from app.utils import DEFAULT_SETTINGS
from extensions import db


class RevivedSettingsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'ENVIRONMENT': 'testing',
            'SECRET_KEY': 'revived-settings-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'TELEGRAM_REMINDER_DAYS': 7,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            owner = User(telegram_id=4242, username='owner', role='user')
            db.session.add(owner)
            db.session.flush()
            db.session.add(Debt(
                user_id=owner.id,
                bank_name='Банк',
                debt_type='credit_card',
                product_name='Карта',
                total_amount=1000,
                remaining_amount=1000,
                next_payment_date=date.today() + timedelta(days=5),
                status='active',
            ))
            db.session.commit()
            self.owner_id = owner.id
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.owner_id)
            session['_fresh'] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _set(self, **values):
        with self.app.app_context():
            for key, value in values.items():
                setting = AppSetting.query.filter_by(key=key).first() or AppSetting(key=key)
                setting.value = str(value)
                db.session.add(setting)
            db.session.commit()

    def _card_state(self):
        html = self.client.get('/').get_data(as_text=True)
        match = re.search(r'debt-card--(overdue|urgent|warning|ok)', html)
        return match.group(1) if match else None

    def test_payment_highlight_follows_warning_and_urgent_days(self):
        self.assertEqual(self._card_state(), 'warning')  # defaults: warning 7, urgent 3

        self._set(urgent_payment_days=5)
        self.assertEqual(self._card_state(), 'urgent')

        self._set(payment_warning_days=4, urgent_payment_days=2)
        self.assertEqual(self._card_state(), 'ok')

    def test_reminder_window_uses_warning_days(self):
        with self.app.app_context():
            self.assertEqual(send_debt_reminders(dry_run=True)['messages'], 1)

        self._set(payment_warning_days=3)
        with self.app.app_context():
            self.assertEqual(send_debt_reminders(dry_run=True)['messages'], 0)

    def test_archive_switch_hides_navigation_and_buttons(self):
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('href="/archive"', html)
        self.assertIn('archiveDebt(', html)

        self._set(archive_enabled='false')
        html = self.client.get('/').get_data(as_text=True)

        self.assertNotIn('href="/archive"', html)
        self.assertNotIn('archiveDebt(', html.split('<script', 1)[0])
        self.assertEqual(self.client.get('/archive').status_code, 404)

    def test_retired_settings_are_gone_from_the_form(self):
        self.assertNotIn('default_currency', DEFAULT_SETTINGS)
        self.assertNotIn('overdue_after_date', DEFAULT_SETTINGS)
        self.assertEqual(set(SETTINGS_FIELDS), set(DEFAULT_SETTINGS))

    def test_settings_form_explains_every_setting(self):
        with self.app.app_context():
            root = User(telegram_id=1, username='root', role='superadmin')
            db.session.add(root)
            db.session.commit()
            root_id = root.id
        with self.client.session_transaction() as session:
            session['_user_id'] = str(root_id)

        html = self.client.get('/admin/settings').get_data(as_text=True)

        for field in SETTINGS_FIELDS.values():
            with self.subTest(key=field['key']):
                self.assertIn(f'name="{field["key"]}"', html)
                self.assertIn(field['help'], html)
        self.assertNotIn('default_currency', html)
        self.assertNotIn('overdue_after_date', html)


if __name__ == '__main__':
    unittest.main()
