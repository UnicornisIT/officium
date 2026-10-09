import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ConfigDefaultsTestCase(unittest.TestCase):
    def test_fail_closed_defaults_and_version_file(self):
        environment = os.environ.copy()
        for key in (
            'OFFICIUM_ENV',
            'SECRET_KEY',
            'TELEGRAM_LOGIN_ENABLED',
            'TELEGRAM_MINI_APP_ENABLED',
            'APP_VERSION',
            'APP_TIMEZONE',
            'DB_USER',
            'SERVER_UPDATE_REPOSITORY',
        ):
            environment.pop(key, None)
        environment['PYTHON_DOTENV_DISABLED'] = '1'
        script = (
            'import json; from config import Config; '
            'print(json.dumps({'
            '"environment": Config.ENVIRONMENT, '
            '"has_secret": bool(Config.SECRET_KEY), '
            '"telegram_login": Config.TELEGRAM_LOGIN_ENABLED, '
            '"telegram_mini_app": Config.TELEGRAM_MINI_APP_ENABLED, '
            '"version": Config.APP_VERSION, '
            '"timezone": Config.APP_TIMEZONE, '
            '"db_user": Config.DB_USER, '
            '"server_update_repository": Config.SERVER_UPDATE_REPOSITORY}))'
        )

        result = subprocess.run(
            [sys.executable, '-c', script],
            cwd=PROJECT_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        defaults = json.loads(result.stdout)

        self.assertEqual(defaults['environment'], 'production')
        self.assertFalse(defaults['has_secret'])
        self.assertFalse(defaults['telegram_login'])
        self.assertFalse(defaults['telegram_mini_app'])
        self.assertEqual(defaults['version'], (PROJECT_ROOT / 'VERSION').read_text().strip())
        self.assertEqual(defaults['timezone'], 'UTC')
        self.assertEqual(defaults['db_user'], 'officium')
        self.assertEqual(defaults['server_update_repository'], '')

    def test_local_environment_example_is_explicit(self):
        values = {}
        for line in (PROJECT_ROOT / '.env.example').read_text(encoding='utf-8').splitlines():
            if not line or line.lstrip().startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            values[key] = value

        self.assertEqual(values['OFFICIUM_ENV'], 'development')
        self.assertEqual(values['DB_ENGINE'], 'sqlite')
        self.assertEqual(values['TELEGRAM_LOGIN_ENABLED'], 'false')
        self.assertEqual(values['TELEGRAM_MINI_APP_ENABLED'], 'false')
        self.assertEqual(values['SERVER_UPDATE_REPOSITORY'], '<repository-owner>/officium')
        self.assertNotIn('APP_VERSION', values)
