import os
import subprocess
import unittest


class RepositoryHygieneTestCase(unittest.TestCase):
    def test_sensitive_local_artifacts_are_not_tracked(self):
        try:
            result = subprocess.run(
                ['git', 'ls-files', '-z'],
                check=False,
                capture_output=True,
            )
        except OSError as exc:
            self.skipTest(f'git is unavailable: {exc}')

        if result.returncode != 0:
            self.skipTest('git ls-files is unavailable in this environment')

        tracked = [
            item.decode('utf-8', errors='surrogateescape').replace('\\', '/')
            for item in result.stdout.split(b'\0')
            if item
        ]
        forbidden = []
        for path in tracked:
            basename = path.rsplit('/', 1)[-1]
            extension = os.path.splitext(basename)[1].lower()
            if extension in {'.db', '.sqlite', '.sqlite3'}:
                forbidden.append(path)
            elif (basename == '.env' or extension == '.env') and basename != '.env.example':
                forbidden.append(path)
            elif basename.startswith('test_results'):
                forbidden.append(path)

        self.assertEqual([], forbidden, f'local artifacts are tracked: {forbidden}')


if __name__ == '__main__':
    unittest.main()
