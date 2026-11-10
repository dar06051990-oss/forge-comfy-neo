"""Offline integration tests for the settings Update action's Git coordinator."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

UPDATER = Path(__file__).resolve().parents[1] / 'runtime' / 'update_from_git.py'


@unittest.skipUnless(shutil.which('git'), 'Git is unavailable')
class GitUpdateTests(unittest.TestCase):
    def git(self, where, *args):
        return subprocess.run(['git', *args], cwd=where, capture_output=True,
                              text=True, check=True).stdout.strip()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.remote = base / 'remote.git'
        self.extension = base / 'forge-comfy-neo'
        self.publisher = base / 'publisher'
        self.git(base, 'init', '--bare', str(self.remote))
        self.git(base, 'clone', str(self.remote), str(self.extension))
        self.git(self.extension, 'config', 'user.name', 'Unit Test')
        self.git(self.extension, 'config', 'user.email', 'test@example.invalid')
        (self.extension / 'runtime').mkdir()
        shutil.copy2(UPDATER, self.extension / 'runtime' / 'update_from_git.py')
        (self.extension / 'README.md').write_text('v1', encoding='utf-8')
        self.git(self.extension, 'add', '.')
        self.git(self.extension, 'commit', '-m', 'initial version')
        self.git(self.extension, 'push', '-u', 'origin', 'HEAD')
        self.git(base, 'clone', str(self.remote), str(self.publisher))
        self.git(self.publisher, 'config', 'user.name', 'Unit Test')
        self.git(self.publisher, 'config', 'user.email', 'test@example.invalid')

    def tearDown(self):
        self.tmp.cleanup()

    def publish(self):
        (self.publisher / 'README.md').write_text('v2', encoding='utf-8')
        self.git(self.publisher, 'add', 'README.md')
        self.git(self.publisher, 'commit', '-m', 'updated version')
        self.git(self.publisher, 'push')

    def update(self):
        return subprocess.run([sys.executable, '-I', str(self.extension / 'runtime' / 'update_from_git.py')],
                              cwd=self.extension, capture_output=True, text=True)

    def test_fast_forward_from_tracked_branch(self):
        self.publish()
        result = self.update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.extension / 'README.md').read_text('utf-8'), 'v2')
        self.assertIn('Restart Forge', result.stdout)

    def test_dirty_tracked_files_are_not_overwritten(self):
        self.publish()
        (self.extension / 'README.md').write_text('local edit', encoding='utf-8')
        result = self.update()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.extension / 'README.md').read_text('utf-8'), 'local edit')
        self.assertIn('Modified tracked files', result.stdout)


if __name__ == '__main__':
    unittest.main()
