"""Explanations next to titles sit in a round "i" hint instead of a paragraph."""

import re
import unittest

from app import create_app
from app.models import User
from extensions import db


PAGES = {
    '/incomes': [
        'Фиксируйте поступления и следите за доходами за месяц.',
        'Финансовый план распределяет деньги до следующей основной выплаты',
    ],
    '/expenses': ['Фиксируйте траты и следите за расходами по категориям.'],
    '/expenses/import': ['Загрузите CSV, XLSX или PDF из Сбера, Т-Банка, Альфа-Банка или ВТБ.'],
    '/finance': [
        'Итог месяца, главные категории и самые заметные списания.',
        'Суммы по конкретным получателям и названиям из выписок.',
    ],
    '/mortgages': ['Отдельная страница для ипотек и крупных долгов.'],
    '/goals': ['Пополнения и снятия — это перемещение ваших денег'],
    '/financial-plan': [
        'на основе доходов, долгов и регулярных расходов.',
        'Рекомендация для каждого поступления, от раннего к позднему',
        'Подушка всегда идет первой.',
        'Из каждого поступления план откладывает долю этой суммы',
    ],
}
ADMIN_PAGES = {
    '/admin/export': ['IP-адреса и данные браузера не включаются.', 'время, пользователь, действие'],
}

HINT = re.compile(
    r'<span class="info-hint" data-info-hint>\s*'
    r'<button type="button" class="info-hint__button" aria-label="Пояснение" aria-expanded="false" '
    r'aria-controls="(?P<id>[\w-]+)" aria-describedby="(?P=id)" data-info-hint-toggle>.*?</button>\s*'
    r'<span class="info-hint__bubble" id="(?P=id)" role="tooltip" hidden>(?P<text>.*?)</span>\s*</span>',
    re.S,
)


class InfoHintTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'ENVIRONMENT': 'testing',
            'SECRET_KEY': 'info-hint-test-secret-over-32-characters',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            member = User(telegram_id=41, username='member', role='user')
            root = User(telegram_id=42, username='root', role='superadmin')
            db.session.add_all([member, root])
            db.session.commit()
            self.ids = {'user': member.id, 'superadmin': root.id}

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _login(self, role):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.ids[role])
            session['_fresh'] = True

    def assert_hints(self, pages):
        for path, texts in pages.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                hints = [match.group('text') for match in HINT.finditer(html)]
                ids = [match.group('id') for match in HINT.finditer(html)]
                self.assertEqual(len(ids), len(set(ids)), 'hint ids must be unique on a page')
                for text in texts:
                    self.assertTrue(any(text in hint for hint in hints), text)
                    # The text is no longer a visible paragraph under the title.
                    self.assertNotRegex(html, r'<p[^>]*>[^<]*' + re.escape(text))

    def test_user_pages_show_explanations_in_hints(self):
        self._login('user')
        self.assert_hints(PAGES)

    def test_admin_pages_show_explanations_in_hints(self):
        self._login('superadmin')
        self.assert_hints(ADMIN_PAGES)

    def test_script_and_styles_ship_the_hint_behaviour(self):
        with open('static/js/app.js', encoding='utf-8') as script:
            source = script.read()
        self.assertIn('function initInfoHints()', source)
        self.assertIn('initInfoHints();', source)
        with open('static/css/style.css', encoding='utf-8') as styles:
            css = styles.read()
        self.assertIn('.info-hint__bubble', css)


if __name__ == '__main__':
    unittest.main()
