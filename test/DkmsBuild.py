# SPDX-License-Identifier: GPL-2.0-only
"""Validate the DKMS build boundary using isolated headers and a recording make."""

import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class Build(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='octa dkms ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.module = self.root / 'module sources'
        self.module.mkdir()
        self.script = self.module / 'dkms-build.sh'
        shutil.copyfile(ROOT / 'tools/dkms-build.sh', self.script)
        (self.module / 'BUILD-SERIES').write_text('6.12\n')
        self.headers = self.root / 'kernel headers'
        (self.headers / 'include/config').mkdir(parents=True)
        (self.headers / 'include/config/kernel.release').write_text('6.12.113+deb13-amd64\n')
        (self.headers / 'Module.symvers').write_text('fixture\n')
        (self.headers / '.config').write_text('CONFIG_CC_VERSION_TEXT="octa-test-cc fixture"\n')
        self.binary = self.root / 'bin'
        self.binary.mkdir()
        self.record = self.root / 'make.json'
        make = self.binary / 'make'
        make.write_text('#!/usr/bin/python3\nimport json,os,sys\n'
                        'open(os.environ["OCTA_TEST_RECORD"],"w").write(json.dumps(sys.argv[1:]))\n')
        make.chmod(0o755)
        for name in ('octa-test-cc', 'octa-override-cc'):
            compiler = self.binary / name
            compiler.write_text('#!/bin/sh\nexit 0\n')
            compiler.chmod(0o755)
        self.environment = dict(os.environ, PATH=str(self.binary) + os.pathsep + os.environ['PATH'],
                                OCTA_TEST_RECORD=str(self.record), OCTA_BUILD_JOBS='2')
        self.environment.pop('KERNEL_CC', None)

    def invoke(self, *arguments):
        return subprocess.run(['bash', str(self.script), str(self.headers), *arguments],
                              capture_output=True, text=True, timeout=3, env=self.environment)

    def test_selected_compiler_strict_flags_parallelism_and_paths_with_spaces(self):
        result = self.invoke('LLVM=1')
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads(self.record.read_text())
        for value in ('-C', str(self.headers), f'M={self.module}', 'CC=octa-test-cc',
                      '-j2', 'W=1', 'KCFLAGS=-Werror', 'LLVM=1', 'modules'):
            self.assertIn(value, arguments)
        self.environment['KERNEL_CC'] = 'octa-override-cc'
        self.assertEqual(self.invoke().returncode, 0)
        self.assertIn('CC=octa-override-cc', json.loads(self.record.read_text()))

    def test_wrong_series_and_unprepared_headers_never_invoke_make(self):
        release = self.headers / 'include/config/kernel.release'
        release.write_text('6.18.54.1-microsoft-standard-WSL2\n')
        self.assertEqual(self.invoke().returncode, 1)
        self.assertFalse(self.record.exists())
        release.write_text('6.12.113+deb13-amd64\n')
        (self.headers / 'Module.symvers').unlink()
        self.assertEqual(self.invoke().returncode, 1)
        self.assertFalse(self.record.exists())

    def test_invalid_jobs_or_missing_compiler_never_invoke_make(self):
        for value in ('0', '65', '01', '-1', '4;id'):
            with self.subTest(value=value):
                self.environment['OCTA_BUILD_JOBS'] = value
                self.assertEqual(self.invoke().returncode, 2)
                self.assertFalse(self.record.exists())
        self.environment['OCTA_BUILD_JOBS'] = '2'
        self.environment['KERNEL_CC'] = 'octa-no-such-compiler'
        self.assertEqual(self.invoke().returncode, 1)
        self.assertFalse(self.record.exists())

    def test_package_version_parser_rejects_unsupported_or_development_sources(self):
        parse = runpy.run_path(str(ROOT / 'tools/package-dkms.py'))['kernel_version']
        source = self.root / 'linux'
        source.mkdir()
        def version(major, minor, patch, extra=''):
            (source / 'Makefile').write_text(f'VERSION = {major}\nPATCHLEVEL = {minor}\n'
                                            f'SUBLEVEL = {patch}\nEXTRAVERSION = {extra}\n')
        version(6, 12, 113)
        self.assertEqual(parse(source), ('6.12.113', '6.12'))
        version(6, 18, 54, '.1')
        self.assertEqual(parse(source), ('6.18.54.1', '6.18'))
        for values in ((6, 19, 0, ''), (6, 12, 0, '-rc1'), ('bad', 12, 113, '')):
            version(*values)
            with self.assertRaises(ValueError):
                parse(source)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]])
