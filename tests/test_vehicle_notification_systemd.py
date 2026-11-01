"""Verify rendered systemd units automatically wherever systemd is available."""
import getpass
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('systemd-analyze'), 'systemd-analyze requires Linux; also runs in CI')
class NotificationSystemdTests(unittest.TestCase):
    def test_rendered_units(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'current').mkdir()
            (root/'shared').mkdir()
            (root/'shared'/'.env').touch()
            replacements = {'@@APP_ROOT@@': root.as_posix(), '@@APP_USER@@': getpass.getuser(),
                '@@APP_GROUP@@': getpass.getuser(), '@@APP_TIMEZONE@@': 'Europe/Moscow',
                '@@VEHICLE_NOTIFICATIONS_HOUR@@': '10', '@@VENV_DIR@@/bin/python': sys.executable}
            paths = []
            for suffix in ['service','timer']:
                name = f'officium-vehicle-notifications.{suffix}'
                content = (ROOT/'deployment'/name).read_text()
                for key,value in replacements.items(): content = content.replace(key,value)
                self.assertNotIn('@@',content)
                path = root/name
                path.write_text(content)
                paths.append(str(path))
            result = subprocess.run(['systemd-analyze','verify',*paths],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result = subprocess.run(['systemd-analyze','calendar','*-*-* 10:00:00 Europe/Moscow'],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
