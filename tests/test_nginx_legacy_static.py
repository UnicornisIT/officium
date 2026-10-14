import importlib.util
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    'nginx_legacy_static', PROJECT_ROOT / 'deployment' / 'nginx_legacy_static.py'
)
helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helper)

LEGACY_SITE = """server {
    listen 80;
    server_name finance.example.org;

    location /static/ {
        alias /opt/officium/static/;
        expires 7d;
    }

    location / {
        proxy_pass http://127.0.0.1:5000;
    }
}
"""


class NginxLegacyStaticTestCase(unittest.TestCase):
    def test_alias_into_the_old_checkout_is_found(self):
        found = helper.offending_directives(LEGACY_SITE, '/opt/officium')
        self.assertEqual(found, [(6, 'alias /opt/officium/static/;')])

    def test_alias_into_current_is_found_because_nginx_cannot_read_it(self):
        text = 'location /static/ { alias /opt/officium/current/static/; }\n'
        self.assertEqual(len(helper.offending_directives(text, '/opt/officium')), 1)

    def test_unrelated_and_commented_paths_are_ignored(self):
        text = (
            'root /var/www/html;\n'
            '# location /static/ { alias /opt/officium/static/; }\n'
            'alias /opt/officium-other/static/;\n'
        )
        self.assertEqual(helper.offending_directives(text, '/opt/officium'), [])

    def test_disable_comments_out_only_the_offending_location(self):
        updated = helper.disable_locations(LEGACY_SITE, '/opt/officium')
        self.assertIn(helper.MARKER + 'location /static/ {', updated)
        self.assertIn(helper.MARKER + 'alias /opt/officium/static/;', updated)
        self.assertIn('    location / {\n        proxy_pass http://127.0.0.1:5000;', updated)
        self.assertEqual(helper.offending_directives(updated, '/opt/officium'), [])
        # Braces stay balanced, so nginx -t accepts the result.
        code = ''.join(line.split('#', 1)[0] for line in updated.splitlines())
        self.assertEqual(code.count('{'), code.count('}'))

    def test_single_line_location_is_disabled(self):
        text = 'server {\n    location /static/ { alias /opt/officium/static/; }\n}\n'
        updated = helper.disable_locations(text, '/opt/officium')
        self.assertEqual(updated.splitlines()[1], helper.MARKER + 'location /static/ { alias /opt/officium/static/; }')

    def test_check_walks_sites_and_reports_each_file_once(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            (config / 'sites-available').mkdir()
            (config / 'sites-enabled').mkdir()
            site = config / 'sites-available' / 'officium'
            site.write_text(LEGACY_SITE, encoding='utf-8')
            (config / 'sites-enabled' / 'officium').write_text(LEGACY_SITE, encoding='utf-8')
            (config / 'nginx.conf').write_text('events {}\n', encoding='utf-8')
            findings = helper.check('/opt/officium', config)
        self.assertEqual(len(findings), 2)
        self.assertTrue(all(number == 6 for _path, number, _directive in findings))

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory) / 'officium'
            site.write_text(LEGACY_SITE, encoding='utf-8')
            self.assertEqual(helper.main(['x', 'check', '/opt/officium', directory]), 1)
            self.assertEqual(helper.main(['x', 'disable', '/opt/officium', str(site)]), 0)
            self.assertEqual(helper.main(['x', 'check', '/opt/officium', directory]), 0)
            self.assertEqual(helper.main(['x']), 2)


if __name__ == '__main__':
    unittest.main()
