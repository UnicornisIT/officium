import unittest

from app import create_app
from app.models import AppSetting, User
from app.utils import telegram_feature_states
from extensions import db


class TelegramEffectiveStateTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
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
        })
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
