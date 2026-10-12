import unittest

from app import create_app
from app.models import ActivityLog, ImpersonationSession, User
from app.time_utils import utc_now
from extensions import db


class AdminJournalFiltersTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'ENVIRONMENT': 'testing',
            'SECRET_KEY': 'journal-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            root = User(telegram_id=1, username='root', role='superadmin')
            anna = User(telegram_id=777001, username='anna_ops', first_name='Анна', role='admin')
            db.session.add_all([root, anna])
            db.session.flush()
            now = utc_now().replace(tzinfo=None)
            record = ImpersonationSession(
                admin_user_id=root.id, actor_label='root #1', target_user_id=anna.id,
                target_label='anna_ops', reason='Проверка', read_only=True,
                started_at=now, expires_at=now,
            )
            db.session.add(record)
            db.session.flush()
            db.session.add_all([
                ActivityLog(user_id=anna.id, action='Заблокировал пользователя', entity_type='user', description='событие-анны'),
                ActivityLog(user_id=None, actor_label='ghost #99', action='Изменил настройки приложения', entity_type='setting', description='событие-призрака'),
                ActivityLog(user_id=root.id, action='Начал impersonate пользователя', entity_type='user', description='событие-входа', impersonation_session_id=record.id),
            ])
            db.session.commit()
            self.root_id, self.anna_id = root.id, anna.id
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.root_id)
            session['_fresh'] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _journal(self, **params):
        response = self.client.get('/admin/logs', query_string=params)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_actor_search_matches_username_name_telegram_id_and_snapshot(self):
        for query in ('anna_ops', 'Анна', '777001'):
            with self.subTest(query=query):
                html = self._journal(actor=query)
                self.assertIn('событие-анны', html)
                self.assertNotIn('событие-призрака', html)
        html = self._journal(actor='ghost')
        self.assertIn('событие-призрака', html)
        self.assertNotIn('событие-анны', html)

    def test_entity_type_filter(self):
        html = self._journal(entity='setting')

        self.assertIn('событие-призрака', html)
        self.assertNotIn('событие-анны', html)
        self.assertIn('Настройка', html)

    def test_impersonation_only_filter(self):
        html = self._journal(impersonation='1')

        self.assertIn('событие-входа', html)
        self.assertNotIn('событие-анны', html)
        self.assertNotIn('событие-призрака', html)

    def test_user_link_filter_keeps_working(self):
        html = self._journal(user=self.anna_id)

        self.assertIn('событие-анны', html)
        self.assertNotIn('событие-входа', html)

    def test_page_no_longer_lists_every_user_in_a_select(self):
        html = self._journal()

        self.assertNotIn('<option value="%d"' % self.anna_id, html)
        self.assertIn('name="actor"', html)


if __name__ == '__main__':
    unittest.main()
