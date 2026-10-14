import importlib.util
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('systemd_units', PROJECT_ROOT / 'deployment' / 'systemd_units.py')
units = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(units)

HEADERS = 'x-forwarded-for x-forwarded-host x-forwarded-proto x-forwarded-port'


def render_unit(app_root='/opt/officium', threads='4', port='5000'):
    template = (PROJECT_ROOT / 'deployment' / 'officium.service.in').read_text(encoding='utf-8')
    for key, value in {
        '@@APP_ROOT@@': app_root, '@@APP_USER@@': 'officium', '@@APP_GROUP@@': 'officium',
        '@@VENV_DIR@@': f'{app_root}/current/venv', '@@HOST@@': '127.0.0.1', '@@PORT@@': port,
        '@@THREADS@@': threads, '@@TRUSTED_PROXY@@': '127.0.0.1', '@@TRUSTED_PROXY_HEADERS@@': HEADERS,
    }.items():
        template = template.replace(key, value)
    return template


# The legacy v0.0.7 unit and the manual work-around drop-ins seen in production.
LEGACY_UNIT = f"""[Service]
User=officium
Group=www-data
WorkingDirectory=/opt/officium
ExecStart=/opt/officium/venv/bin/waitress-serve --listen=127.0.0.1:5000 --threads=8 \\
    --trusted-proxy=127.0.0.1 "--trusted-proxy-headers={HEADERS}" run:app
"""
STALE_DROPIN = """[Service]
ExecStart=
ExecStart=/opt/officium/current/venv/bin/waitress-serve --listen=127.0.0.1:5000 run:app
"""
WORKAROUND_DROPIN = f"""[Service]
ExecStart=
ExecStart=/opt/officium/current/venv/bin/python -m waitress --listen=127.0.0.1:5000 --threads=4 --trusted-proxy=127.0.0.1 "--trusted-proxy-headers={HEADERS}" run:app
"""


class UnitParamsTestCase(unittest.TestCase):
    def test_params_come_from_the_legacy_console_script_unit(self):
        params = units.unit_params(f'# /etc/systemd/system/officium.service\n{LEGACY_UNIT}')
        self.assertEqual(params, {
            'APP_USER': 'officium', 'APP_GROUP': 'www-data', 'VENV_DIR': '/opt/officium/venv',
            'HOST': '127.0.0.1', 'PORT': '5000', 'THREADS': '8', 'TRUSTED_PROXY': '127.0.0.1',
            'TRUSTED_PROXY_HEADERS': HEADERS,
        })

    def test_the_drop_in_command_wins_over_the_unit(self):
        cat = (
            f'# /etc/systemd/system/officium.service\n{render_unit(threads="2")}\n'
            f'# /etc/systemd/system/officium.service.d/override.conf\n{WORKAROUND_DROPIN}'
        )
        params = units.unit_params(cat)
        self.assertEqual(params['THREADS'], '4')
        self.assertEqual(params['VENV_DIR'], '/opt/officium/current/venv')
        self.assertEqual(params['TRUSTED_PROXY_HEADERS'], HEADERS)

    def test_rendered_unit_runs_python_m_waitress(self):
        commands = units.effective_exec_start([render_unit()])
        self.assertEqual(units.exec_argv(commands[-1])[:3], ['/opt/officium/current/venv/bin/python', '-m', 'waitress'])


class DropinClassificationTestCase(unittest.TestCase):
    def classify(self, dropin, new_unit=None, exists=lambda _path: True):
        return units.classify_dropin(dropin, new_unit or render_unit(), '/opt/officium', path_exists=exists)

    def test_production_work_around_equal_to_the_new_unit_is_removed(self):
        decision, reason = self.classify(WORKAROUND_DROPIN)
        self.assertEqual(decision, 'remove')
        self.assertIn('exactly what the new unit runs', reason)

    def test_work_around_with_different_settings_is_kept(self):
        decision, reason = self.classify(WORKAROUND_DROPIN, new_unit=render_unit(threads='8'))
        self.assertEqual(decision, 'keep')
        self.assertIn('different command', reason)

    def test_console_script_drop_in_is_removed(self):
        decision, reason = self.classify(STALE_DROPIN)
        self.assertEqual(decision, 'remove')
        self.assertIn('console script', reason)

    def test_drop_in_into_the_old_checkout_is_removed(self):
        dropin = '[Service]\nExecStart=\nExecStart=/opt/officium/venv/bin/python -m waitress run:app\n'
        decision, reason = self.classify(dropin)
        self.assertEqual(decision, 'remove')
        self.assertIn('old checkout', reason)
        decision, reason = self.classify(dropin, exists=lambda _path: False)
        self.assertIn('does not exist', reason)

    def test_drop_in_with_other_settings_is_kept_even_when_stale(self):
        decision, reason = self.classify(STALE_DROPIN + 'Environment=FOO=bar\n')
        self.assertEqual(decision, 'keep')
        self.assertIn('Environment', reason)

    def test_drop_in_without_exec_start_is_ignored(self):
        self.assertIsNone(self.classify('[Service]\nLimitNOFILE=4096\n')[0])

    def test_cli_lists_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'officium.service').write_text(render_unit(), encoding='utf-8')
            dropins = root / 'officium.service.d'
            dropins.mkdir()
            (dropins / 'override.conf').write_text(WORKAROUND_DROPIN, encoding='utf-8')
            (dropins / 'limits.conf').write_text('[Service]\nLimitNOFILE=4096\n', encoding='utf-8')
            from io import StringIO
            from contextlib import redirect_stdout
            output = StringIO()
            with redirect_stdout(output):
                code = units.main(['x', 'dropins', str(root / 'officium.service'), str(dropins), '/opt/officium'])
        self.assertEqual(code, 0)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith('remove\t'))
        self.assertIn('override.conf', lines[0])


if __name__ == '__main__':
    unittest.main()
