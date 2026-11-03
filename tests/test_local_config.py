"""Windows-created UTF-8 BOM .env files must not silently select production."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dotenv import load_dotenv as real_load_dotenv


class LocalConfigTests(unittest.TestCase):
    def test_local_env_with_or_without_windows_bom(self):
        source = Path(__file__).resolve().parents[1]/'config.py'
        for encoding in ['utf-8', 'utf-8-sig']:
            with self.subTest(encoding=encoding), tempfile.TemporaryDirectory() as directory:
                env_file = Path(directory)/'.env'
                env_file.write_text('OFFICIUM_ENV=development\nDB_ENGINE=sqlite\nFLASK_DEBUG=true\nDEV_LOGIN_ENABLED=true\n', encoding=encoding)
                with patch.dict(os.environ, {}, clear=True), patch('dotenv.load_dotenv',
                        side_effect=lambda **kwargs: real_load_dotenv(env_file, **kwargs)):
                    spec = importlib.util.spec_from_file_location('local_config_under_test', source)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    self.assertEqual(module.Config.ENVIRONMENT, 'development')
                    self.assertEqual(module.Config.DB_ENGINE, 'sqlite')
                    self.assertTrue(module.Config.DEBUG)
                    self.assertTrue(module.Config.DEV_LOGIN_ENABLED)
