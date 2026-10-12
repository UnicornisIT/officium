import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

from app import create_app
from app.models import User
from app.time_utils import utc_now
from extensions import db


class AdminOverviewTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'ENVIRONMENT': 'testing',
            'SECRET_KEY': 'overview-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'SERVER_UPDATE_ENABLED': False,
        })
        self.client = self.app.test_client()
        now = utc_now().replace(tzinfo=None)
        with self.app.app_context():
            db.create_all()
            root = User(telegram_id=1, username='root', role='superadmin', created_at=now - timedelta(days=90))
            db.session.add_all([
                root,
                User(telegram_id=2, username='fresh', created_at=now - timedelta(days=2)),
                User(telegram_id=3, username='month', created_at=now - timedelta(days=20)),
                User(telegram_id=4, username='old', created_at=now - timedelta(days=60), is_blocked=True),
            ])
            db.session.commit()
            self.root_id = root.id
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.root_id)
            session['_fresh'] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _card(self, html, label):
        start = html.index(f'admin-stat-card__label">{label}')
        return html[start:start + 400]

    def test_overview_counts_accounts_and_new_registrations(self):
        html = self.client.get('/admin').get_data(as_text=True)

        self.assertIn('<strong>4</strong>', self._card(html, 'Пользователи'))
        new_card = self._card(html, 'Новые за 7 дней')
        self.assertIn('<strong>1</strong>', new_card)
        self.assertIn('За 30 дней: 2', new_card)
        self.assertIn('<strong>1</strong>', self._card(html, 'Заблокированные'))
        self.assertIn('<strong>0</strong>', self._card(html, 'Вход от имени сейчас'))
        self.assertIn('Обновление выключено', html)

    def test_active_impersonation_sessions_are_counted(self):
        observer = self.app.test_client()
        with observer.session_transaction() as session:
            session['_user_id'] = str(self.root_id)
        with self.app.app_context():
            fresh_id = User.query.filter_by(username='fresh').one().id
        self.client.post(f'/admin/impersonate/{fresh_id}', data={'reason': 'Проверка обзора'})

        html = observer.get('/admin').get_data(as_text=True)

        self.assertIn('<strong>1</strong>', self._card(html, 'Вход от имени сейчас'))

    def test_database_error_shows_a_readable_state(self):
        failure = OperationalError('SELECT', {}, Exception('down'))
        with patch('app.services.impersonation_service.active_count', side_effect=failure):
            response = self.client.get('/admin')

        self.assertEqual(response.status_code, 200)
        self.assertIn('База данных недоступна', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
