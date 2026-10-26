# SPDX-License-Identifier: GPL-2.0-only
"""Check test-kernel patch idempotence without a kernel tree or privileges."""

import fcntl
from functools import partial
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / 'tools/build-kernel-vm.py'
BUILD = runpy.run_path(str(SCRIPT))
RUN = partial(subprocess.run, check=True, capture_output=True)


class Patch(unittest.TestCase):
    def test_unapplied_and_applied_patch_are_distinguished_without_reversal(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            sample = source / 'sample'
            sample.write_text('before\n')
            patch = source / 'change.patch'
            patch.write_text('--- a/sample\n+++ b/sample\n@@ -1 +1 @@\n-before\n+after\n')
            BUILD['prepare_source'](source, patch, RUN)
            self.assertEqual(sample.read_text(), 'after\n')
            BUILD['prepare_source'](source, patch, RUN)
            self.assertEqual(sample.read_text(), 'after\n')

    def test_mismatch_does_not_modify_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            sample = source / 'sample'
            sample.write_text('unrelated\n')
            patch = source / 'change.patch'
            patch.write_text('--- a/sample\n+++ b/sample\n@@ -1 +1 @@\n-before\n+after\n')
            with self.assertRaises(subprocess.CalledProcessError):
                BUILD['prepare_source'](source, patch, RUN)
            self.assertEqual(sample.read_text(), 'unrelated\n')

    def test_partial_patch_state_is_rejected_before_either_file_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / 'first').write_text('after\n')
            (source / 'second').write_text('before\n')
            patch = source / 'change.patch'
            patch.write_text('--- a/first\n+++ b/first\n@@ -1 +1 @@\n-before\n+after\n'
                             '--- a/second\n+++ b/second\n@@ -1 +1 @@\n-before\n+after\n')
            with self.assertRaises(subprocess.CalledProcessError):
                BUILD['prepare_source'](source, patch, RUN)
            self.assertEqual((source / 'first').read_text(), 'after\n')
            self.assertEqual((source / 'second').read_text(), 'before\n')


class Build(unittest.TestCase):
    def test_default_configuration_preserves_the_existing_minimal_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            path = BUILD['configuration'](output, False)
            self.assertEqual(path, BUILD['ROOT'] / 'kernel/vm.config')
            self.assertEqual(list(output.iterdir()), [])

    def test_power_profile_keeps_base_input_unchanged_and_uses_a_distinct_release(self):
        base = BUILD['ROOT'] / 'kernel/vm.config'
        original = base.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            path = BUILD['configuration'](Path(directory), True)
            lines = path.read_text().splitlines()
            self.assertEqual(path.parent, Path(directory))
            self.assertTrue(all(option in lines for option in BUILD['PM_REQUIRED']))
            self.assertTrue(all(option in lines for option in BUILD['REQUIRED']))
            self.assertEqual([line for line in lines if line.startswith('CONFIG_LOCALVERSION=')],
                             ['CONFIG_LOCALVERSION="-pm-octa-kasan-vm"'])
        self.assertEqual(base.read_bytes(), original)

    def test_running_build_lock_prevents_source_changes_and_another_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / 'source', root / 'output'
            source.mkdir()
            output.mkdir()
            makefile = 'VERSION = 6\nPATCHLEVEL = 12\n'
            (source / 'Makefile').write_text(makefile)
            marker = output / 'module-path'
            marker.write_text('existing build\n')
            with (output / 'build.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = subprocess.run([sys.executable, str(SCRIPT), str(source), str(output)],
                                        capture_output=True, text=True, timeout=3)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('BlockingIOError', result.stderr)
            self.assertEqual((source / 'Makefile').read_text(), makefile)
            self.assertEqual(marker.read_text(), 'existing build\n')
            self.assertFalse((output / 'kernel-build').exists())


if __name__ == '__main__':
    unittest.main()
