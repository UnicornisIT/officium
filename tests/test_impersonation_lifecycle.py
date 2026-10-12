import unittest
from datetime import timedelta

from app import create_app
from app.models import ActivityLog, AppSetting, Debt, ImpersonationSession, User
from app.services.impersonation_service import CHANGE_ACTION, END_ACTION, START_ACTION, START_TEST_ACTION
from app.time_utils import utc_now
from extensions import db


REASON = 'Проверка обращения в поддержку'


class ImpersonationLifecycleTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'ENVIRONMENT': 'testing',
            'SECRET_KEY': 'impersonation-lifecycle-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'ADMIN_LOGIN_ENABLED': True,
            'TEST_USER_ENABLED': True,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            root = User(telegram_id=1, username='root', first_name='Анна', role='superadmin')
            other_root = User(telegram_id=2, username='root2', role='superadmin')
            helper = User(telegram_id=3, username='helper', role='admin')
            member = User(telegram_id=4, username='member', first_name='Олег', last_name='Петров', role='user')
            blocked = User(telegram_id=5, username='blocked', role='user', is_blocked=True)
            db.session.add_all([root, other_root, helper, member, blocked])
            db.session.commit()
            self.ids = {
                'root': root.id, 'other_root': other_root.id, 'helper': helper.id,
                'member': member.id, 'blocked': blocked.id,
            }

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _login(self, user_id):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True

    def _session(self):
        with self.client.session_transaction() as session:
            return dict(session)

    def _start(self, target='member', reason=REASON, **extra):
        return self.client.post(f'/admin/impersonate/{self.ids[target]}', data={'reason': reason, **extra})

    def test_reason_is_required_and_validated(self):
        self._login(self.ids['root'])

        for reason in ('', '   ', 'abc', 'x' * 201):
            with self.subTest(reason=reason[:10]):
                self._start(reason=reason)
                self.assertEqual(self._session().get('_user_id'), str(self.ids['root']))
        with self.app.app_context():
            self.assertEqual(ImpersonationSession.query.count(), 0)

    def test_forbidden_for_blocked_users_and_superadmins(self):
        self._login(self.ids['root'])

        for target in ('blocked', 'other_root'):
            with self.subTest(target=target):
                self._start(target=target)
                self.assertEqual(self._session().get('_user_id'), str(self.ids['root']))

    def test_ordinary_admin_cannot_impersonate(self):
        self._login(self.ids['helper'])

        self._start()

        self.assertEqual(self._session().get('_user_id'), str(self.ids['helper']))
        with self.app.app_context():
            self.assertEqual(ImpersonationSession.query.count(), 0)

    def test_start_and_end_are_logged_with_reason_actor_and_ip(self):
        self._login(self.ids['root'])
        self.client.environ_base['REMOTE_ADDR'] = '203.0.113.5'

        self._start()
        self.client.post('/admin/stop-impersonate')

        with self.app.app_context():
            record = ImpersonationSession.query.one()
            self.assertEqual(record.reason, REASON)
            self.assertTrue(record.read_only)
            self.assertEqual(record.end_reason, 'manual')
            self.assertEqual(record.ip_start, '203.0.113.5')
            self.assertEqual(record.ip_end, '203.0.113.5')
            start = ActivityLog.query.filter_by(action=START_ACTION).one()
            end = ActivityLog.query.filter_by(action=END_ACTION).one()
            self.assertEqual(start.user_id, self.ids['root'])
            self.assertIn(REASON, start.description)
            self.assertEqual(start.ip_address, '203.0.113.5')
            self.assertEqual(start.impersonation_session_id, record.id)
            self.assertEqual(end.user_id, self.ids['root'])
            self.assertEqual(end.impersonation_session_id, record.id)
        self.assertEqual(self._session().get('_user_id'), str(self.ids['root']))

    def test_allow_changes_checkbox_is_recorded(self):
        self._login(self.ids['root'])

        self._start(allow_changes='on')

        with self.app.app_context():
            self.assertFalse(ImpersonationSession.query.one().read_only)

    def test_banner_names_the_user_on_every_page_including_mini_app(self):
        self._login(self.ids['root'])
        self._start()

        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('impersonation-banner', html)
        self.assertIn('Олег Петров', html)
        self.assertIn('только просмотр', html)

        with self.client.session_transaction() as session:
            session['telegram_mini_app'] = True
        mini_html = self.client.get('/').get_data(as_text=True)
        self.assertIn('telegram-mini-app', mini_html)
        self.assertIn('impersonation-banner', mini_html)

    def test_session_returns_to_admin_after_timeout(self):
        with self.app.app_context():
            db.session.add(AppSetting(key='impersonation_timeout_minutes', value='10'))
            db.session.commit()
        self._login(self.ids['root'])
        self._start()
        with self.app.app_context():
            record = ImpersonationSession.query.one()
            self.assertAlmostEqual(
                (record.expires_at - record.started_at).total_seconds(), 600, delta=2,
            )
            record.expires_at = (utc_now() - timedelta(seconds=1)).replace(tzinfo=None)
            db.session.commit()

        response = self.client.get('/')

        self.assertEqual(response.headers['Location'], '/admin')
        self.assertEqual(self._session().get('_user_id'), str(self.ids['root']))
        self.assertNotIn('impersonation_session_id', self._session())
        with self.app.app_context():
            self.assertEqual(ImpersonationSession.query.one().end_reason, 'timeout')
            self.assertEqual(ActivityLog.query.filter_by(action=END_ACTION).count(), 1)

    def test_api_requests_get_json_after_timeout(self):
        self._login(self.ids['root'])
        self._start()
        with self.app.app_context():
            record = ImpersonationSession.query.one()
            record.expires_at = (utc_now() - timedelta(seconds=1)).replace(tzinfo=None)
            db.session.commit()

        response = self.client.get('/api/debts')

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()['code'], 'impersonation_ended')

    def test_logout_closes_the_session_record(self):
        self._login(self.ids['root'])
        self._start()

        self.client.get('/logout')

        with self.app.app_context():
            self.assertEqual(ImpersonationSession.query.one().end_reason, 'logout')
        self.assertIsNone(self._session().get('_user_id'))

    def test_blocking_the_target_ends_the_session(self):
        self._login(self.ids['root'])
        self._start()
        with self.app.app_context():
            db.session.get(User, self.ids['member']).is_blocked = True
            db.session.commit()

        response = self.client.get('/')

        self.assertEqual(response.headers['Location'], '/admin')
        with self.app.app_context():
            self.assertEqual(ImpersonationSession.query.one().end_reason, 'blocked')

    def test_emergency_admin_can_impersonate_and_return(self):
        self._login('admin')

        self._start()
        self.assertEqual(self._session().get('_user_id'), str(self.ids['member']))
        self.client.post('/admin/stop-impersonate')

        self.assertEqual(self._session().get('_user_id'), 'admin')
        with self.app.app_context():
            record = ImpersonationSession.query.one()
            self.assertIsNone(record.admin_user_id)
            self.assertEqual(record.actor_label, 'Аварийный администратор')
            end = ActivityLog.query.filter_by(action=END_ACTION).one()
            self.assertEqual(end.actor_label, 'Аварийный администратор')

    def test_test_user_needs_no_reason_but_is_audited(self):
        self._login(self.ids['root'])

        self.client.post('/admin/impersonate/test')

        with self.app.app_context():
            record = ImpersonationSession.query.one()
            self.assertIsNone(record.reason)
            self.assertEqual(ActivityLog.query.filter_by(action=START_TEST_ACTION).count(), 1)
        self.assertIn('impersonation_session_id', self._session())


    DEBT_PAYLOAD = {
        'bank_name': 'Банк для проверки',
        'debt_type': 'credit_card',
        'product_name': 'Карта',
        'total_amount': '73519',
        'remaining_amount': '73519',
        'next_payment_date': '2026-11-15',
    }

    def test_read_only_mode_blocks_changes(self):
        self._login(self.ids['root'])
        self._start()

        api = self.client.post('/api/debts', json=self.DEBT_PAYLOAD)
        form = self.client.post('/expenses', data={'amount': '100'})

        self.assertEqual(api.status_code, 403)
        self.assertEqual(api.get_json()['code'], 'impersonation_read_only')
        self.assertEqual(form.status_code, 303)
        with self.app.app_context():
            self.assertEqual(Debt.query.count(), 0)
            self.assertEqual(ActivityLog.query.filter_by(action=CHANGE_ACTION).count(), 0)
        # Leaving the session is always allowed.
        self.client.post('/admin/stop-impersonate')
        self.assertEqual(self._session().get('_user_id'), str(self.ids['root']))

    def test_changes_on_behalf_are_logged_without_amounts(self):
        self._login(self.ids['root'])
        self._start(allow_changes='on')

        response = self.client.post('/api/debts', json=self.DEBT_PAYLOAD)

        self.assertLess(response.status_code, 400, response.get_data(as_text=True))
        with self.app.app_context():
            debt = Debt.query.one()
            self.assertEqual(debt.user_id, self.ids['member'])
            entry = ActivityLog.query.filter_by(action=CHANGE_ACTION).one()
            self.assertEqual(entry.user_id, self.ids['root'])
            self.assertEqual(entry.entity_id, self.ids['member'])
            self.assertIsNotNone(entry.impersonation_session_id)
            self.assertIn('POST /api/debts', entry.description)
            self.assertNotIn('73519', entry.description)
            self.assertNotIn('73 519', entry.description)


if __name__ == '__main__':
    unittest.main()
