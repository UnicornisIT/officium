import unittest

from app import create_app
from app.models import ActivityLog, User
from extensions import db


class AdminUserManagementTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'ENVIRONMENT': 'testing',
            'SECRET_KEY': 'user-management-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'ADMIN_LOGIN_ENABLED': True,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            users = {
                'root': User(telegram_id=1, username='root', role='superadmin'),
                'helper': User(telegram_id=2, username='helper', role='admin'),
                'helper2': User(telegram_id=3, username='helper2', role='admin'),
                'member': User(telegram_id=4, username='member', role='user'),
                'nameless': User(telegram_id=5, username=None, role='user'),
            }
            db.session.add_all(users.values())
            db.session.commit()
            self.ids = {key: user.id for key, user in users.items()}

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _login(self, key):
        with self.client.session_transaction() as session:
            session['_user_id'] = 'admin' if key == 'emergency' else str(self.ids[key])
            session['_fresh'] = True

    def _user(self, key):
        with self.app.app_context():
            user = db.session.get(User, self.ids[key])
            return None if user is None else (user.role, user.is_blocked)

    def _post(self, key, **data):
        return self.client.post(f'/admin/users/{self.ids[key]}', data=data)

    def test_admin_manages_only_plain_users(self):
        self._login('helper')

        self._post('member', action='block')
        self._post('helper2', action='block')

        self.assertEqual(self._user('member'), ('user', True))
        self.assertEqual(self._user('helper2'), ('admin', False))

    def test_admin_cannot_change_roles_or_delete(self):
        self._login('helper')

        self._post('member', action='set_role', role='admin')
        self._post('member', action='delete', confirm_username='member')

        self.assertEqual(self._user('member'), ('user', False))

    def test_superadmin_changes_role_with_one_select(self):
        self._login('root')

        self._post('member', action='set_role', role='admin')
        self.assertEqual(self._user('member'), ('admin', False))
        self._post('member', action='set_role', role='user')

        self.assertEqual(self._user('member'), ('user', False))
        with self.app.app_context():
            actions = [entry.action for entry in ActivityLog.query.order_by(ActivityLog.id).all()]
        self.assertEqual(actions, ['Назначил администратора', 'Снял административные права'])

    def test_last_superadmin_cannot_be_demoted_blocked_or_deleted(self):
        self._login('emergency')

        self._post('root', action='set_role', role='admin')
        self._post('root', action='block')
        self._post('root', action='delete', confirm_username='root')

        self.assertEqual(self._user('root'), ('superadmin', False))

    def test_deletion_requires_typing_the_username(self):
        self._login('root')

        for typed in ('', 'Member', 'someone'):
            with self.subTest(typed=typed):
                response = self._post('member', action='delete', confirm_username=typed)
                self.assertEqual(response.status_code, 302)
                self.assertIsNotNone(self._user('member'))

        response = self._post('member', action='delete', confirm_username='member')
        self.assertEqual(response.headers['Location'], '/admin/users')
        self.assertIsNone(self._user('member'))

    def test_user_without_username_is_confirmed_by_telegram_id(self):
        self._login('root')

        html = self.client.get(f'/admin/users/{self.ids["nameless"]}').get_data(as_text=True)
        self.assertIn('data-admin-confirm-match="5"', html)
        self._post('nameless', action='delete', confirm_username='5')

        self.assertIsNone(self._user('nameless'))

    def test_card_shows_actions_matching_the_role(self):
        self._login('helper')
        member_card = self.client.get(f'/admin/users/{self.ids["member"]}').get_data(as_text=True)
        admin_card = self.client.get(f'/admin/users/{self.ids["helper2"]}').get_data(as_text=True)

        self.assertIn('value="block"', member_card)
        self.assertNotIn('name="role"', member_card)
        self.assertNotIn('Опасная зона', member_card)
        self.assertNotIn('Войти как пользователь', member_card)
        self.assertNotIn('value="block"', admin_card)

        self._login('root')
        root_card = self.client.get(f'/admin/users/{self.ids["member"]}').get_data(as_text=True)
        self.assertIn('name="role"', root_card)
        self.assertIn('Опасная зона', root_card)
        self.assertIn('Войти как пользователь', root_card)

    def test_users_can_be_filtered_by_active_impersonation(self):
        self._login('root')
        self.client.post(f'/admin/impersonate/{self.ids["member"]}', data={'reason': 'Проверка фильтра'})
        # Another administrator, in another browser, looks at the list.
        observer = self.app.test_client()
        with observer.session_transaction() as session:
            session['_user_id'] = str(self.ids['helper'])
            session['_fresh'] = True

        html = observer.get('/admin/users?status=impersonated').get_data(as_text=True)

        self.assertIn('member', html)
        self.assertNotIn('helper2', html)


if __name__ == '__main__':
    unittest.main()
