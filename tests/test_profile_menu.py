"""Avatar menu in the header and the /profile placeholder page."""

import re
import unittest

from app import create_app
from app.models import User
from extensions import db


IMPERSONATION_REASON = 'Проверка обращения в поддержку'


def _user_menu(html):
    match = re.search(r'<ul class="dropdown-menu[^"]*user-menu__panel".*?</ul>', html, re.S)
    return match.group(0) if match else ''


class ProfileMenuTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'ENVIRONMENT': 'testing',
            'SECRET_KEY': 'profile-menu-secret-over-32-characters',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'ADMIN_LOGIN_ENABLED': True,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            root = User(telegram_id=1, username='root', first_name='Анна', role='superadmin')
            helper = User(telegram_id=2, username='helper', role='admin')
            member = User(telegram_id=3, username='member', first_name='олег', role='user')
            nameless = User(telegram_id=4, role='user')
            photo = User(
                telegram_id=5, username='photo', first_name='Фото', role='user',
                photo_url='https://t.me/i/userpic/320/photo.jpg',
            )
            unsafe_photo = User(
                telegram_id=6, username='unsafe', first_name='Юля', role='user',
                photo_url='javascript:alert(1)',
            )
            db.session.add_all([root, helper, member, nameless, photo, unsafe_photo])
            db.session.commit()
            self.ids = {
                'root': root.id, 'helper': helper.id, 'member': member.id,
                'nameless': nameless.id, 'photo': photo.id, 'unsafe_photo': unsafe_photo.id,
            }

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _login(self, key):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.ids[key])
            session['_fresh'] = True

    def test_profile_requires_login(self):
        response = self.client.get('/profile')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_profile_shows_name_initial_and_placeholder(self):
        self._login('member')

        html = self.client.get('/profile').get_data(as_text=True)

        self.assertIn('Профиль в разработке. Скоро здесь можно будет рассказать о себе и настроить аккаунт.', html)
        self.assertIn('<h1 id="profileTitle">олег</h1>', html)
        self.assertIn('data-avatar-initial="О"', html)

    def test_initial_falls_back_to_username_then_letter_o(self):
        self._login('helper')
        self.assertIn('data-avatar-initial="H"', self.client.get('/profile').get_data(as_text=True))

        self._login('nameless')
        html = self.client.get('/profile').get_data(as_text=True)
        self.assertIn('data-avatar-initial="O"', html)
        self.assertIn('<h1 id="profileTitle">Пользователь</h1>', html)

    def test_avatar_uses_https_photo_only(self):
        self._login('photo')
        html = self.client.get('/profile').get_data(as_text=True)
        self.assertIn('src="https://t.me/i/userpic/320/photo.jpg"', html)
        self.assertIn('has-photo', html)

        self._login('unsafe_photo')
        html = self.client.get('/profile').get_data(as_text=True)
        self.assertNotIn('javascript:alert', html)
        self.assertIn('data-avatar-initial="Ю"', html)

    def test_header_menu_replaces_standalone_admin_theme_and_logout(self):
        self._login('member')

        html = self.client.get('/').get_data(as_text=True)
        menu = _user_menu(html)

        self.assertIn('id="userMenuToggle"', html)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn('href="/profile"', menu)
        self.assertIn('id="themeToggleBtn"', menu)
        self.assertIn('href="/logout"', menu)
        self.assertNotIn('data-admin-panel-link', html)
        self.assertNotIn('Панель администратора', html)
        self.assertEqual(html.count('id="themeToggleBtn"'), 1)

    def test_admin_sees_admin_panel_item_in_menu_only(self):
        for key in ('root', 'helper'):
            with self.subTest(user=key):
                self._login(key)
                html = self.client.get('/').get_data(as_text=True)
                menu = _user_menu(html)

                self.assertIn('data-admin-panel-link', menu)
                self.assertIn('bi-shield-lock', menu)
                navbar_without_menu = html.split('<nav class="navbar', 1)[1].split('</nav>', 1)[0].replace(menu, '')
                self.assertNotIn('Панель администратора', navbar_without_menu)

    def test_admin_panel_stays_closed_for_plain_user(self):
        self._login('member')

        for path in ('/admin', '/admin/users', '/admin/settings'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertIn(response.status_code, (302, 403))
                if response.status_code == 302:
                    self.assertNotIn('/admin', response.headers['Location'])

    def test_impersonation_hides_admin_item_and_keeps_banner(self):
        self._login('root')
        response = self.client.post(
            f'/admin/impersonate/{self.ids["member"]}', data={'reason': IMPERSONATION_REASON},
        )
        self.assertEqual(response.status_code, 302)

        html = self.client.get('/').get_data(as_text=True)

        self.assertIn('impersonation-banner', html)
        self.assertIn('action="/admin/stop-impersonate"', html)
        self.assertIn('id="userMenuToggle"', html)
        self.assertNotIn('data-admin-panel-link', html)

    def test_analytics_and_plan_links_live_on_the_dashboard_not_in_the_header(self):
        self._login('member')

        html = self.client.get('/').get_data(as_text=True)
        navbar = html.split('<nav class="navbar', 1)[1].split('</nav>', 1)[0]

        self.assertNotIn('Куда уходят деньги', navbar)
        self.assertNotIn('Финансовый план', navbar)
        self.assertIn('href="/finance" data-dashboard-shortcut="finance"', html)
        self.assertIn('href="/financial-plan" data-dashboard-shortcut="plan"', html)

    def test_mini_app_avatar_links_to_profile_and_hides_admin_for_users(self):
        self._login('member')
        with self.client.session_transaction() as session:
            session['telegram_mini_app'] = True

        html = self.client.get('/').get_data(as_text=True)

        self.assertIn('class="telegram-app-bar__profile" href="/profile"', html)
        self.assertIn('telegram-more-menu__profile-avatar', html)
        self.assertNotIn('data-admin-panel-link', html)

        self._login('root')
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('data-admin-panel-link', html)

    def test_anonymous_login_page_keeps_theme_toggle(self):
        html = self.client.get('/login').get_data(as_text=True)

        self.assertNotIn('id="userMenuToggle"', html)


if __name__ == '__main__':
    unittest.main()
