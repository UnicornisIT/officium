import re
import unittest

from app import create_app
from app.models import AppSetting
from app.services.telegram_frame_service import frame_ancestors
from extensions import db
from tests.test_telegram_mini_app import BOT_TOKEN, build_init_data


BASE_URL = 'https://officium.test'
FRAME_NAVIGATION = {'Sec-Fetch-Dest': 'iframe', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Site': 'cross-site'}
FRAME_FETCH = {'Sec-Fetch-Dest': 'empty', 'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Site': 'same-origin'}
TOP_LEVEL = {'Sec-Fetch-Dest': 'document', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Site': 'none'}


def set_cookies(response):
    return {header.split('=', 1)[0]: header for header in response.headers.getlist('Set-Cookie')}


class FrameAncestorsConfigTestCase(unittest.TestCase):
    def test_parses_space_or_comma_separated_origins(self):
        config = {'TELEGRAM_WEB_FRAME_ANCESTORS': 'https://web.telegram.org, https://webk.telegram.org'}

        self.assertEqual(frame_ancestors(config), ('https://web.telegram.org', 'https://webk.telegram.org'))

    def test_drops_wildcards_and_keywords(self):
        config = {'TELEGRAM_WEB_FRAME_ANCESTORS': "* 'unsafe-inline' https: https://web.telegram.org"}

        self.assertEqual(frame_ancestors(config), ('https://web.telegram.org',))


class TelegramWebFrameTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'telegram-web-frame-test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': True,
            'SESSION_COOKIE_SECURE': True,
            'TELEGRAM_BOT_TOKEN': BOT_TOKEN,
            'TELEGRAM_BOT_USERNAME': 'officium_test_bot',
            'TELEGRAM_LOGIN_ENABLED': True,
            'TELEGRAM_MINI_APP_ENABLED': True,
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

    def open_in_frame(self):
        response = self.client.get('/telegram-app', base_url=BASE_URL, headers=FRAME_NAVIGATION)
        match = re.search(r'<meta name="csrf-token" content="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match)
        return response, match.group(1)

    def login_in_frame(self, csrf_token):
        return self.client.post(
            '/auth/telegram-mini-app',
            base_url=BASE_URL,
            json={'init_data': build_init_data()},
            headers={**FRAME_FETCH, 'X-CSRFToken': csrf_token, 'Referer': f'{BASE_URL}/telegram-app'},
        )

    def test_regular_pages_keep_same_origin_framing_and_lax_cookie(self):
        response = self.client.get('/telegram-app', base_url=BASE_URL, headers=TOP_LEVEL)

        self.assertEqual(response.headers['X-Frame-Options'], 'SAMEORIGIN')
        self.assertNotIn('Content-Security-Policy', response.headers)
        cookie = set_cookies(response)['session']
        self.assertIn('SameSite=Lax', cookie)
        self.assertNotIn('Partitioned', cookie)
        self.assertNotIn('officium_tg_frame', set_cookies(response))

    def test_cross_site_frame_of_mini_app_allows_only_telegram_web(self):
        response, _token = self.open_in_frame()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('X-Frame-Options', response.headers)
        self.assertEqual(
            response.headers['Content-Security-Policy'],
            "frame-ancestors 'self' https://web.telegram.org",
        )

    def test_frame_uses_its_own_partitioned_cookie(self):
        response, _token = self.open_in_frame()

        cookies = set_cookies(response)
        self.assertNotIn('session', cookies)
        frame_cookie = cookies['officium_tg_frame']
        for attribute in ('Secure', 'HttpOnly', 'SameSite=None', 'Partitioned', 'Path=/'):
            self.assertIn(attribute, frame_cookie)

    def test_frame_session_logs_in_and_keeps_framing_after_redirect(self):
        _response, token = self.open_in_frame()

        login = self.login_in_frame(token)
        self.assertEqual(login.status_code, 200, login.get_data(as_text=True)[:200])
        self.assertTrue(login.get_json()['success'])

        dashboard = self.client.get(login.get_json()['next_url'], base_url=BASE_URL, headers={
            **FRAME_NAVIGATION, 'Sec-Fetch-Site': 'same-origin',
        })
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotIn('X-Frame-Options', dashboard.headers)
        self.assertIn('frame-ancestors', dashboard.headers['Content-Security-Policy'])
        self.assertIn('telegram-mini-app', dashboard.get_data(as_text=True))

    def test_csrf_token_is_still_required_in_frame(self):
        self.open_in_frame()

        response = self.client.post(
            '/auth/telegram-mini-app',
            base_url=BASE_URL,
            json={'init_data': build_init_data()},
            headers={**FRAME_FETCH, 'Referer': f'{BASE_URL}/telegram-app'},
        )

        self.assertEqual(response.status_code, 400)

    def test_frame_token_is_useless_without_the_frame_cookie(self):
        _response, token = self.open_in_frame()
        self.client.delete_cookie('officium_tg_frame', domain='officium.test')

        response = self.login_in_frame(token)

        self.assertEqual(response.status_code, 400)

    def test_top_level_session_is_separate_from_frame_session(self):
        _response, token = self.open_in_frame()
        self.assertEqual(self.login_in_frame(token).status_code, 200)

        top_level = self.client.get('/', base_url=BASE_URL, headers=TOP_LEVEL)

        self.assertEqual(top_level.status_code, 302)
        self.assertIn('/login', top_level.headers['Location'])
        self.assertEqual(top_level.headers['X-Frame-Options'], 'SAMEORIGIN')

    def test_other_pages_in_cross_site_frame_without_mini_app_session_are_blocked(self):
        response = self.client.get('/login', base_url=BASE_URL, headers=FRAME_NAVIGATION)

        self.assertEqual(response.headers['X-Frame-Options'], 'SAMEORIGIN')
        self.assertNotIn('Content-Security-Policy', response.headers)
        self.assertNotIn('officium_tg_frame', set_cookies(response))

    def test_same_origin_frame_keeps_regular_session(self):
        response = self.client.get('/telegram-app', base_url=BASE_URL, headers={
            **FRAME_NAVIGATION, 'Sec-Fetch-Site': 'same-origin',
        })

        self.assertEqual(response.headers['X-Frame-Options'], 'SAMEORIGIN')
        self.assertIn('session', set_cookies(response))

    def test_empty_frame_ancestors_disables_telegram_web_framing(self):
        self.app.config['TELEGRAM_WEB_FRAME_ANCESTORS'] = ''

        response = self.client.get('/telegram-app', base_url=BASE_URL, headers=FRAME_NAVIGATION)

        self.assertEqual(response.headers['X-Frame-Options'], 'SAMEORIGIN')
        self.assertIn('session', set_cookies(response))


if __name__ == '__main__':
    unittest.main()
