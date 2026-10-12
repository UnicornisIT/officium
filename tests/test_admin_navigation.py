"""Every admin page works for each role and links only to pages that role can open."""

import re
import unittest
from html import unescape
from urllib.parse import urldefrag

from app import create_app
from app.models import ActivityLog, DictionaryEntry, User
from extensions import db


SUPERADMIN_ONLY = ('/admin/settings', '/admin/dictionaries', '/admin/export', '/admin/system', '/admin/server-update')
HREF_RE = re.compile(r'href="(/admin[^"]*)"')


class AdminNavigationTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'ENVIRONMENT': 'testing',
            'SECRET_KEY': 'admin-navigation-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'ADMIN_LOGIN_ENABLED': True,
            'SERVER_UPDATE_ENABLED': False,
        })
        with self.app.app_context():
            db.create_all()
            root = User(telegram_id=1, username='root', role='superadmin')
            helper = User(telegram_id=2, username='helper', role='admin')
            member = User(telegram_id=3, username='member', role='user')
            db.session.add_all([root, helper, member])
            db.session.flush()
            db.session.add(DictionaryEntry(dictionary_type='bank', value='Банк'))
            db.session.add(ActivityLog(user_id=helper.id, action='Заблокировал пользователя', entity_type='user', entity_id=member.id))
            db.session.commit()
            self.session_ids = {'superadmin': str(root.id), 'admin': str(helper.id), 'emergency': 'admin'}

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _client(self, role):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = self.session_ids[role]
            session['_fresh'] = True
        return client

    def _crawl(self, role):
        client = self._client(role)
        queue, seen, statuses = ['/admin'], set(), {}
        while queue:
            path = queue.pop()
            if path in seen:
                continue
            seen.add(path)
            response = client.get(path)
            statuses[path] = response.status_code
            if response.status_code == 200 and response.mimetype == 'text/html':
                for href in HREF_RE.findall(response.get_data(as_text=True)):
                    target = urldefrag(unescape(href))[0]
                    if target and '/admin/login' not in target and target not in seen:
                        queue.append(target)
        return statuses

    def test_every_linked_admin_page_opens_for_each_role(self):
        for role in ('superadmin', 'admin', 'emergency'):
            with self.subTest(role=role):
                statuses = self._crawl(role)
                broken = {path: code for path, code in statuses.items() if code != 200}
                self.assertEqual(broken, {})
                self.assertIn('/admin/users', statuses)
                self.assertIn('/admin/logs', statuses)

    def test_admin_role_sees_no_superadmin_sections(self):
        statuses = self._crawl('admin')

        for path in SUPERADMIN_ONLY:
            self.assertNotIn(path, statuses)

    def test_superadmin_and_emergency_reach_every_system_section(self):
        for role in ('superadmin', 'emergency'):
            with self.subTest(role=role):
                statuses = self._crawl(role)
                for path in SUPERADMIN_ONLY:
                    self.assertEqual(statuses.get(path), 200, path)

    def test_emergency_admin_is_marked_in_the_header(self):
        html = self._client('emergency').get('/admin').get_data(as_text=True)

        self.assertIn('Аварийный вход', html)

    def test_return_to_app_link_is_hidden_outside_dev_mode(self):
        html = self._client('superadmin').get('/admin').get_data(as_text=True)

        self.assertNotIn('Вернуться в приложение', html)


if __name__ == '__main__':
    unittest.main()
