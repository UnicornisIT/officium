import unittest
from unittest.mock import Mock, patch

from app import create_app
from app.models import AppSetting, User
from extensions import db


class GoogleOAuthTestCase(unittest.TestCase):
    def setUp(self):
        self.google = Mock()
        self.google.authorize_redirect.return_value = ('redirected', 302)
        with patch('app.routes.auth.OAuth.register', return_value=self.google):
            self.app = create_app({
                'TESTING': True,
                'SECRET_KEY': 'google-oauth-test-secret-over-32-characters',
                'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
                'SQLALCHEMY_ENGINE_OPTIONS': {},
                'WTF_CSRF_ENABLED': False,
                'GOOGLE_LOGIN_ENABLED': True,
                'GOOGLE_CLIENT_ID': 'synthetic-client-id',
                'GOOGLE_CLIENT_SECRET': 'synthetic-client-secret',
                'GOOGLE_REDIRECT_URI': 'https://example.test/auth/google/callback',
            })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            db.session.add(AppSetting(key='registration_enabled', value='true'))
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_unauthenticated_google_entry_is_reachable_and_uses_fixed_redirect(self):
        response = self.client.get('/auth/google')
        self.assertEqual(response.status_code, 302)
        self.google.authorize_redirect.assert_called_once_with(
            'https://example.test/auth/google/callback'
        )

    def test_verified_google_profile_creates_session_without_real_network(self):
        self.google.authorize_access_token.return_value = {'access_token': 'synthetic'}
        profile_response = Mock()
        profile_response.json.return_value = {
            'sub': 'google-user-123',
            'email': 'person@example.test',
            'email_verified': True,
            'name': 'Test Person',
            'picture': 'https://images.example.test/avatar.png',
        }
        self.google.get.return_value = profile_response

        response = self.client.get('/auth/google/callback')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/'))
        self.google.get.assert_called_once_with(
            'https://openidconnect.googleapis.com/v1/userinfo'
        )
        with self.app.app_context():
            user = User.query.filter_by(google_id='google-user-123').one()
            self.assertEqual(user.email, 'person@example.test')
            self.assertEqual(user.role, 'user')
            self.assertEqual(user.login_count, 1)

    def test_unverified_email_is_rejected(self):
        self.google.authorize_access_token.return_value = {'access_token': 'synthetic'}
        profile_response = Mock()
        profile_response.json.return_value = {
            'sub': 'unverified-user',
            'email': 'unverified@example.test',
            'email_verified': False,
        }
        self.google.get.return_value = profile_response
        response = self.client.get('/auth/google/callback')
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(User.query.filter_by(google_id='unverified-user').first())


if __name__ == '__main__':
    unittest.main()
