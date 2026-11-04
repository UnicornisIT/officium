import unittest

from app import create_app
from app.models import AppSetting, User
from app.utils import telegram_feature_states
from extensions import db


class TelegramEffectiveStateTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(
            {
                'TESTING': True,
                'SECRET_KEY': 'telegram-effective-state-test',
                'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
                'SQLALCHEMY_ENGINE_OPTIONS': {},
                'WTF_CSRF_ENABLED': False,
                'TELEGRAM_LOGIN_ENABLED': False,
                'TELEGRAM_MINI_APP_ENABLED': True,
                'TELEGRAM_BOT_ENABLED': True,
                'TELEGRAM_BOT_TOKEN': 'synthetic-token',
                'TELEGRAM_BOT_USERNAME': 'synthetic_bot',
                'TELEGRAM_WEBHOOK_SECRET': 'synthetic-webhook-secret',
                'OFFICIUM_PUBLIC_URL': 'https://example.test',
            }
        )
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            db.session.add(AppSetting(key='telegram_login_enabled', value='true'))
            admin = User(telegram_id=-9201, username='root', role='superadmin')
            db.session.add(admin)
            db.session.commit()
            self.admin_id = admin.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_database_setting_and_environment_form_one_effective_state(self):
        with self.app.app_context():
            states = telegram_feature_states(self.app.config)
        self.assertTrue(states['login']['enabled_in_settings'])
        self.assertTrue(states['login']['configuration_valid'])
        self.assertTrue(states['login']['effective'])
        self.assertTrue(states['mini_app']['effective'])
        self.assertTrue(states['bot']['effective'])

    def test_missing_credentials_never_reports_effective(self):
        self.app.config['TELEGRAM_BOT_TOKEN'] = ''
        with self.app.app_context():
            states = telegram_feature_states(self.app.config)
        self.assertTrue(states['login']['enabled_in_settings'])
        self.assertFalse(states['login']['configuration_valid'])
        self.assertFalse(states['login']['effective'])

    def settings_form(self):
        from app.routes.admin import SETTINGS_FIELDS, _setting_default

        with self.app.app_context():
            from app.utils import get_setting

            return {
                key: get_setting(key, _setting_default(key)) for key in SETTINGS_FIELDS
            }

    def test_admin_switch_persists_and_overrides_environment(self):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.admin_id)
            session['_fresh'] = True
        self.assertIn(
            'Команды и ввод операций',
            self.client.get('/admin/settings').get_data(as_text=True),
        )
        form = self.settings_form()
        form['telegram_bot_enabled'] = 'false'
        self.assertEqual(
            self.client.post('/admin/settings', data=form).status_code, 302
        )
        with self.app.app_context():
            self.assertFalse(
                telegram_feature_states(self.app.config)['bot']['effective']
            )
            self.assertEqual(
                AppSetting.query.filter_by(key='telegram_bot_enabled').one().value,
                'false',
            )
        self.app.config['TELEGRAM_BOT_ENABLED'] = False
        form['telegram_bot_enabled'] = 'true'
        self.assertEqual(
            self.client.post('/admin/settings', data=form).status_code, 302
        )
        with self.app.app_context():
            self.assertTrue(
                telegram_feature_states(self.app.config)['bot']['effective']
            )
            self.assertTrue(
                telegram_feature_states(self.app.config)['login']['effective']
            )

    def test_regular_user_cannot_change_bot_switch(self):
        with self.app.app_context():
            user = User(telegram_id=123, role='user')
            db.session.add(user)
            db.session.commit()
            user_id = user.id
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True
        self.assertIn(
            self.client.post('/admin/settings', data=self.settings_form()).status_code,
            (302, 403),
        )
        with self.app.app_context():
            self.assertIsNone(
                AppSetting.query.filter_by(key='telegram_bot_enabled').first()
            )

    def test_enabling_without_receive_credentials_shows_validation_error(self):
        self.app.config.update(TELEGRAM_BOT_ENABLED=False, TELEGRAM_BOT_TOKEN='')
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.admin_id)
            session['_fresh'] = True
        form = self.settings_form()
        form['telegram_bot_enabled'] = 'true'
        response = self.client.post('/admin/settings', data=form)
        self.assertEqual(response.status_code, 200)
        self.assertIn('настройте токен', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(
                AppSetting.query.filter_by(key='telegram_bot_enabled').first()
            )

    def test_polling_does_not_require_webhook_secret(self):
        self.app.config.update(
            TELEGRAM_RECEIVE_MODE='polling', TELEGRAM_WEBHOOK_SECRET=''
        )
        with self.app.app_context():
            self.assertTrue(
                telegram_feature_states(self.app.config)['bot']['effective']
            )

    def test_missing_public_url_prevents_enabling_webhook_commands(self):
        self.app.config.update(TELEGRAM_BOT_ENABLED=False, OFFICIUM_PUBLIC_URL='')
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.admin_id)
            session['_fresh'] = True
        form = self.settings_form()
        form['telegram_bot_enabled'] = 'true'
        response = self.client.post('/admin/settings', data=form)
        self.assertEqual(response.status_code, 200)
        self.assertIn('настройте токен', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertFalse(
                telegram_feature_states(self.app.config)['bot']['configuration_valid']
            )
            self.assertIsNone(
                AppSetting.query.filter_by(key='telegram_bot_enabled').first()
            )

    def test_system_page_separates_configured_valid_and_verified(self):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.admin_id)
            session['_fresh'] = True
        html = self.client.get('/admin/system').get_data(as_text=True)
        self.assertIn('Включено в настройках: да', html)
        self.assertIn('конфигурация корректна: да', html)
        self.assertIn('проверено в Telegram: не проверено', html)


if __name__ == '__main__':
    unittest.main()
