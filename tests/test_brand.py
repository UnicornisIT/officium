import json
import struct
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from flask import render_template

from app import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC = PROJECT_ROOT / 'static'


class BrandAssetsTestCase(unittest.TestCase):
    def test_brand_svgs_are_valid_and_free_of_currency_signs(self):
        for path in (
            STATIC / 'favicon.svg',
            STATIC / 'images' / 'brand' / 'logo.svg',
            STATIC / 'images' / 'brand' / 'logo-mark.svg',
            STATIC / 'images' / 'brand' / 'logo-light.svg',
            STATIC / 'images' / 'brand' / 'logo-dark.svg',
            STATIC / 'images' / 'brand' / 'app-icon.svg',
        ):
            with self.subTest(path=path.name):
                text = path.read_text(encoding='utf-8')
                root = ElementTree.fromstring(text)
                self.assertTrue(root.tag.endswith('svg'))
                self.assertNotIn('₽', text)
                # The wordmark is outlined, so the logo never depends on a loaded font.
                self.assertNotIn('<text', text)

    def test_favicon_ico_contains_small_png_sizes(self):
        data = (STATIC / 'favicon.ico').read_bytes()
        reserved, kind, count = struct.unpack_from('<HHH', data, 0)
        self.assertEqual((reserved, kind), (0, 1))
        sizes = sorted(data[6 + 16 * index] for index in range(count))
        self.assertEqual(sizes, [16, 32, 48])

    def test_raster_icons_have_expected_dimensions(self):
        for name, size in (
            ('favicon-16x16.png', 16),
            ('favicon-32x32.png', 32),
            ('apple-touch-icon.png', 180),
            ('icon-192.png', 192),
            ('icon-512.png', 512),
        ):
            with self.subTest(name=name):
                data = (STATIC / name).read_bytes()
                self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
                width, height = struct.unpack('>II', data[16:24])
                self.assertEqual((width, height), (size, size))


class BrandMarkupTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'ENVIRONMENT': 'testing',
            'SECRET_KEY': 'brand-test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        self.client = self.app.test_client()

    def test_public_layout_uses_inline_mark_and_theme_color(self):
        html = self.client.get('/login').get_data(as_text=True)

        self.assertIn('<span class="brand-icon"><svg class="brand-mark"', html)
        self.assertIn('<meta name="theme-color" content="#f4f6fb" media="(prefers-color-scheme: light)">', html)
        self.assertIn('<meta name="theme-color" content="#08101c" media="(prefers-color-scheme: dark)">', html)
        self.assertNotIn('officium-mark.svg', html)

    def test_admin_login_uses_mark_with_wordmark(self):
        html = self.client.get('/admin/login').get_data(as_text=True)

        self.assertIn('admin-login-card__logo', html)
        self.assertIn('class="brand-mark', html)

    def test_root_icons_are_public_and_not_redirected_to_login(self):
        for path, name, content_type in (
            ('/favicon.ico', 'favicon.ico', 'image/'),
            ('/apple-touch-icon.png', 'apple-touch-icon.png', 'image/png'),
            ('/apple-touch-icon-precomposed.png', 'apple-touch-icon.png', 'image/png'),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.content_type.startswith(content_type), response.content_type)
                self.assertEqual(response.get_data(), (STATIC / name).read_bytes())
                response.close()

    def test_manifest_lists_existing_192_and_512_icons(self):
        response = self.client.get('/static/site.webmanifest')
        self.assertEqual(response.status_code, 200)
        manifest = json.loads(response.get_data(as_text=True))
        response.close()
        sizes = {icon['sizes'] for icon in manifest['icons']}
        self.assertTrue({'192x192', '512x512'} <= sizes)
        for icon in manifest['icons']:
            self.assertTrue((PROJECT_ROOT / icon['src'].lstrip('/')).is_file(), icon['src'])

    def test_every_layout_links_the_icon_set(self):
        with self.app.test_request_context('/telegram-app'):
            mini_app = render_template('telegram_app.html', bot_username='', mini_app_short_name='')
        for html in (self.client.get('/login').get_data(as_text=True), self.client.get('/admin/login').get_data(as_text=True), mini_app):
            self.assertIn('favicon.ico?v=', html)
            self.assertIn('favicon.svg?v=', html)
            self.assertIn('apple-touch-icon.png?v=', html)
            self.assertIn('rel="manifest"', html)
            self.assertNotIn('sizes="any"', html)

    def test_theme_switch_keeps_browser_theme_color_in_sync(self):
        script = (STATIC / 'js' / 'app.js').read_text(encoding='utf-8')

        self.assertIn('meta[name="theme-color"]', script)


if __name__ == '__main__':
    unittest.main()
