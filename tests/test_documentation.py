import unittest
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, unquote

from app import create_app
from extensions import db


class DocumentationTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'documentation-test-secret',
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
        self.assertNotIn('mailto:', html)
        self.assertNotIn('github.com/', html)
        self.assertNotIn('site-footer__primary-link', html)

    def test_all_sections_and_current_images(self):
        html = self.client.get('/documentation').get_data(as_text=True)
        sections = re.findall(r'<section class="docs-section" id="([^"]+)" data-docs-section>', html)
        self.assertEqual(len(sections), 12)
        self.assertEqual(len(set(sections)), 12)
        for section in sections:
            self.assertIn(f'href="#{section}"', html)
            self.assertIn(f'value="#{section}"', html)
        for text in ['Профиль и личные настройки', 'Автомобили', 'Цели и финансовая подушка',
                     'GRX130-0000001', 'IANA', 'раз в минуту', 'Денежные графики']:
            self.assertIn(text, html)
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root/'static/images/documentation/manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(len(manifest['images']), 18)
        for image in manifest['images']:
            path = root/'static/images/documentation'/image['file']
            self.assertTrue(path.is_file(), str(path))
            self.assertGreater(path.stat().st_size, 1000)
        sources = re.findall(r'<img src="([^"]+)"', html)
        self.assertGreaterEqual(len(sources), 14)
        for source in sources:
            parsed = urlsplit(source.replace('&amp;', '&'))
            self.assertIn('guide-20261104-notifications-1', parsed.query)
            self.assertTrue((root/unquote(parsed.path).lstrip('/')).is_file())


if __name__ == '__main__':
    unittest.main()
