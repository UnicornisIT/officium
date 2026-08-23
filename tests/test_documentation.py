import unittest

from app import create_app
from extensions import db


class DocumentationTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_documentation_is_available_without_login(self):
        response = self.client.get('/documentation')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Как работает officium', html)
        self.assertIn('id="quick-start"', html)
        self.assertIn('id="financial-plan"', html)
        self.assertIn('плановая сумма досрочного погашения', html)
        self.assertIn('Зарплата, аванс или другой доход', html)
        self.assertIn('получит собственную рекомендацию по распределению', html)
        self.assertIn('ближайшие расходы с отметкой «Ежемесячный»', html)
        self.assertIn('учитываются точные даты, комиссии', html)
        self.assertIn('итог распределения сверяется с суммой дохода', html)
        self.assertIn('невыполненная часть плана месяца', html)
        self.assertIn('id="faq"', html)

    def test_footer_links_to_documentation(self):
        response = self.client.get('/login')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/documentation"', html)
        self.assertIn('Документация', html)
        self.assertLess(html.index('site-footer__docs-button'), html.index('bi-github'))
        self.assertNotIn('site-footer__primary-link', html)


if __name__ == '__main__':
    unittest.main()
