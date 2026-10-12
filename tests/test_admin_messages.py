import unittest
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

from app import create_app
from app.models import DictionaryEntry, User
from extensions import db


class AdminMessagesTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'ENVIRONMENT': 'testing',
            'SECRET_KEY': 'admin-messages-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            root = User(telegram_id=1, username='root', role='superadmin')
            member = User(telegram_id=2, username='member', role='user')
            entry = DictionaryEntry(dictionary_type='bank', value='Банк Проверочный')
            db.session.add_all([root, member, entry])
            db.session.commit()
            self.root_id, self.member_id, self.entry_id = root.id, member.id, entry.id
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.root_id)
            session['_fresh'] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_redirects_carry_no_message_in_the_url(self):
        responses = (
            self.client.post('/admin/dictionaries', data={'dictionary_type': 'bank', 'value': 'Новый банк'}),
            self.client.post(f'/admin/dictionaries/{self.entry_id}/delete'),
            self.client.post(f'/admin/users/{self.member_id}', data={'action': 'block'}),
        )
        for response in responses:
            with self.subTest(location=response.headers.get('Location')):
                self.assertEqual(response.status_code, 302)
                self.assertNotIn('success=', response.headers['Location'])

    def test_message_is_shown_once_inside_the_admin_shell(self):
        self.client.post(f'/admin/users/{self.member_id}', data={'action': 'block'})

        first = self.client.get(f'/admin/users/{self.member_id}').get_data(as_text=True)
        second = self.client.get(f'/admin/users/{self.member_id}').get_data(as_text=True)

        self.assertIn('admin-flash-stack', first)
        self.assertIn('Изменения сохранены.', first)
        self.assertNotIn('Изменения сохранены.', second)

    def test_query_string_text_is_not_reflected(self):
        html = self.client.get(f'/admin/users/{self.member_id}?success=Ваш аккаунт взломан').get_data(as_text=True)

        self.assertNotIn('Ваш аккаунт взломан', html)

    def test_dictionary_delete_survives_database_errors(self):
        with patch('app.routes.admin.db.session.commit', side_effect=OperationalError('DELETE', {}, Exception('down'))):
            response = self.client.post(f'/admin/dictionaries/{self.entry_id}/delete', follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Не удалось удалить элемент справочника', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(DictionaryEntry, self.entry_id))

    def test_missing_dictionary_entry_is_a_warning(self):
        response = self.client.post('/admin/dictionaries/9999/delete', follow_redirects=True)

        self.assertIn('Элемент справочника не найден', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
