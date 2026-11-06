import unittest

from flask import template_rendered

from app import create_app
from app.models import User
from extensions import db


class AdminUserSearchWildcardTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(
            {
                'TESTING': True,
                'ENVIRONMENT': 'testing',
                'SECRET_KEY': 'admin-search-secret',
                'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
                'SQLALCHEMY_ENGINE_OPTIONS': {},
                'WTF_CSRF_ENABLED': False,
            }
        )
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            admin = User(telegram_id=1, username='root', role='admin')
            db.session.add(admin)
            db.session.add_all(
                [
                    User(telegram_id=2, username='alice', role='user'),
                    User(telegram_id=3, username='bob', role='user'),
                    User(telegram_id=4, username='carol', role='user'),
                ]
            )
            db.session.commit()
            admin_id = admin.id
        with self.client.session_transaction() as session:
            session['_user_id'] = str(admin_id)
            session['_fresh'] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _search(self, term):
        results = []

        def capture(sender, template, context, **extra):
            results.append(
                (
                    [user.username for user in context['users']],
                    context['pagination'].total,
                    context['q'],
                )
            )

        with template_rendered.connected_to(capture, self.app):
            response = self.client.get('/admin/users', query_string={'q': term})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(results), 1)
        usernames, total, query = results[0]
        self.assertEqual(total, len(usernames))
        self.assertEqual(query, term)
        return usernames

    def test_wildcards_do_not_match_ordinary_users(self):
        for term in ('%', '_'):
            with self.subTest(term=term):
                self.assertEqual(self._search(term), [])

    def test_special_characters_match_literally_in_each_name_field(self):
        for field in ('username', 'first_name', 'last_name'):
            for term in ('%', '_', '\\', '\\%', '\\_'):
                with self.subTest(field=field, term=term):
                    with self.app.app_context():
                        user = User.query.filter_by(username='alice').one()
                        setattr(user, field, f'before{term}after')
                        db.session.commit()
                        expected_username = user.username
                    try:
                        self.assertEqual(self._search(term), [expected_username])
                    finally:
                        with self.app.app_context():
                            user = User.query.filter_by(
                                username=expected_username
                            ).one()
                            setattr(
                                user, field, 'alice' if field == 'username' else None
                            )
                            db.session.commit()

    def test_plain_search_remains_case_insensitive_and_partial(self):
        self.assertEqual(self._search('LIC'), ['alice'])

    def test_telegram_id_remains_searchable(self):
        self.assertEqual(self._search('3'), ['bob'])


if __name__ == '__main__':
    unittest.main()
