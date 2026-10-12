import unittest

from app import create_app
from app.models import User
from app.services.contact_service import ContactValidationError, load_contacts, validate_contact
from extensions import db


class ContactValidationTestCase(unittest.TestCase):
    def test_accepts_supported_addresses_and_builds_safe_href(self):
        telegram = validate_contact({
            'type': 'telegram', 'label': 'Поддержка', 'address': '@safe_support',
            'icon': 'telegram', 'published': True, 'locations': ['footer'], 'order': 2,
        })
        self.assertEqual(telegram['href'], 'https://t.me/safe_support')
        self.assertTrue(telegram['published'])

    def test_rejects_dangerous_uri_local_targets_and_credentials(self):
        for address in (
            'javascript:alert(1)', 'http://example.org', 'https://127.0.0.1/help',
            'https://user:pass@example.org/', 'https://localhost/help',
        ):
            with self.subTest(address=address), self.assertRaises(ContactValidationError):
                validate_contact({
                    'type': 'other', 'label': 'Bad', 'address': address,
                    'icon': 'link-45deg', 'locations': ['footer'], 'order': 0,
                })

    def test_rejects_unknown_icon_and_location(self):
        with self.assertRaises(ContactValidationError):
            validate_contact({
                'type': 'website', 'label': 'Site', 'address': 'https://example.org',
                'icon': 'bi-script-injection', 'locations': ['admin'], 'order': 0,
            })


class ContactAdminRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'contacts-test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'ENVIRONMENT': 'development',
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            superadmin = User(telegram_id=-9101, role='superadmin', username='root')
            admin = User(telegram_id=-9102, role='admin', username='admin')
            db.session.add_all([superadmin, admin])
            db.session.commit()
            self.superadmin_id = superadmin.id
            self.admin_id = admin.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True

    def test_superadmin_contact_is_published_without_restart_and_xss_is_escaped(self):
        self.login(self.superadmin_id)
        response = self.client.post('/admin/settings/contacts', data={
            'action': 'save', 'type': 'website', 'label': '<script>alert(1)</script>',
            'address': 'https://example.org/help', 'icon': 'globe2',
            'published': '1', 'locations': ['footer', 'login'], 'order': '1',
        })
        self.assertEqual(response.status_code, 302)
        self.client.get('/logout')
        footer = self.client.get('/login').get_data(as_text=True)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', footer)
        self.assertNotIn('<script>alert(1)</script>', footer)
        self.assertIn('rel="noopener noreferrer nofollow"', footer)
        with self.app.app_context():
            self.assertEqual(len(load_contacts(published_only=True, location='footer')), 1)

    def test_ordinary_admin_cannot_mutate_contacts(self):
        self.login(self.admin_id)
        response = self.client.post('/admin/settings/contacts', data={
            'action': 'save', 'type': 'website', 'label': 'Site',
            'address': 'https://example.org', 'icon': 'globe2',
            'locations': ['footer'], 'order': '0',
        })
        self.assertEqual(response.status_code, 403)
        with self.app.app_context():
            self.assertEqual(load_contacts(), [])

    def test_contact_mutation_requires_csrf(self):
        self.login(self.superadmin_id)
        self.app.config['WTF_CSRF_ENABLED'] = True
        response = self.client.post('/admin/settings/contacts', data={
            'action': 'delete', 'contact_id': 'missing',
        })
        self.assertEqual(response.status_code, 400)

    def test_icon_picker_renders_visible_icon_previews_and_native_form_value(self):
        self.login(self.superadmin_id)

        response = self.client.get('/admin/settings')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="contactIconSelect" name="icon"', html)
        self.assertEqual(html.count('class="dropdown-item admin-icon-picker__option"'), 7)
        self.assertIn('class="bi bi-telegram"', html)
        self.assertIn('data-icon-label="Ссылка"', html)


if __name__ == '__main__':
    unittest.main()
