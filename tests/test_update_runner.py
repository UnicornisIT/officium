import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.runner_loader import load_runner


runner = load_runner()

BUILD_ROOT = '/opt/officium/releases/.build-v1-abc/v1-0123456789ab'
FINAL_ROOT = '/opt/officium/releases/v1-0123456789ab'


def make_release(base, root):
    """A release whose venv was created at ``root`` but now sits at ``base``."""
    bin_dir = base / 'venv' / 'bin'
    bin_dir.mkdir(parents=True)
    (bin_dir / 'waitress-serve').write_text(
        f'#!{root}/venv/bin/python\nimport sys\nfrom waitress.runner import run\n', encoding='utf-8'
    )
    (bin_dir / 'activate').write_text(f"VIRTUAL_ENV='{root}/venv'\nexport VIRTUAL_ENV\n", encoding='utf-8')
    (bin_dir / 'activate.fish').write_text(f'set -gx VIRTUAL_ENV "{root}/venv"\n', encoding='utf-8')
    (base / 'venv' / 'pyvenv.cfg').write_text(
        f'home = /usr/bin\ncommand = /usr/bin/python3 -m venv {root}/venv\n', encoding='utf-8'
    )
    return base


class VenvRelocationTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.release = make_release(Path(self.temp.name) / 'release', BUILD_ROOT)
        if not hasattr(os, 'chown'):  # Windows developer machines
            patcher = mock.patch.object(runner.os, 'chown', create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_moved_release_with_build_shebangs_is_rejected(self):
        with self.assertRaises(runner.UpdateFailure) as caught:
            runner.verify_release_venv(self.release, 'venv', expected_root=FINAL_ROOT)
        self.assertEqual(caught.exception.code, 'deployment_failed')
        self.assertIn('waitress-serve', str(caught.exception))
        self.assertIn('.build-v1-abc', str(caught.exception))

    def test_relocation_points_every_script_at_the_final_path(self):
        runner.relocate_release_venv(self.release, BUILD_ROOT, FINAL_ROOT, 'venv')
        runner.verify_release_venv(self.release, 'venv', expected_root=FINAL_ROOT)
        script = (self.release / 'venv' / 'bin' / 'waitress-serve').read_text(encoding='utf-8')
        self.assertTrue(script.startswith(f'#!{FINAL_ROOT}/venv/bin/python\n'))
        self.assertIn(f"VIRTUAL_ENV='{FINAL_ROOT}/venv'", (self.release / 'venv' / 'bin' / 'activate').read_text())
        self.assertIn(f'-m venv {FINAL_ROOT}/venv', (self.release / 'venv' / 'pyvenv.cfg').read_text())
        self.assertNotIn(BUILD_ROOT, (self.release / 'venv' / 'bin' / 'activate.fish').read_text())

    def test_relocation_is_idempotent_and_keeps_unrelated_files(self):
        (self.release / 'venv' / 'bin' / 'notes').write_text('no paths here\n', encoding='utf-8')
        runner.relocate_release_venv(self.release, BUILD_ROOT, FINAL_ROOT, 'venv')
        runner.relocate_release_venv(self.release, BUILD_ROOT, FINAL_ROOT, 'venv')
        runner.verify_release_venv(self.release, 'venv', expected_root=FINAL_ROOT)
        self.assertEqual((self.release / 'venv' / 'bin' / 'notes').read_text(), 'no paths here\n')

    def test_a_sibling_release_path_is_not_accepted_as_inside(self):
        runner.relocate_release_venv(self.release, BUILD_ROOT, FINAL_ROOT + '-old', 'venv')
        with self.assertRaises(runner.UpdateFailure):
            runner.verify_release_venv(self.release, 'venv', expected_root=FINAL_ROOT)

    def test_venv_name_is_detected(self):
        self.assertEqual(runner.release_venv_name(self.release), 'venv')
        with self.assertRaises(runner.UpdateFailure):
            runner.release_venv_name(Path(self.temp.name))


if __name__ == '__main__':
    unittest.main()
