# SPDX-License-Identifier: GPL-2.0-only
"""Check fixed CLI rejection cases that cannot open an ALSA PCM or change hardware."""

import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


BINARY = Path(sys.argv.pop(1)).resolve()


class CommandLine(unittest.TestCase):
    def invoke(self, arguments):
        return subprocess.run([str(BINARY), *arguments], text=True, capture_output=True,
                              check=False, timeout=5)

    def test_help_does_not_require_devices(self):
        result = self.invoke(['--help'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--experimental RATE BASE_CARD BASE_SERIAL EXP_CARD EXP_SERIAL', result.stdout)

    def test_syntax_must_be_explicit_and_rate_must_parse_completely(self):
        suffix = ['BASE', 'BASE_SERIAL', 'EXP', 'EXP_SERIAL']
        for arguments in ([], ['96000', *suffix], ['--experimental'],
                          *(['--experimental', rate, *suffix] for rate in
                            ('', '-1', '+96000', '96000 ', '96000junk', '4294967296'))):
            with self.subTest(arguments=arguments):
                result = self.invoke(arguments)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertIn('Usage:', result.stderr)

    @unittest.skipIf(os.geteuid() == 0, 'The CLI intentionally rejects root before parsing device parameters.')
    def test_invalid_identity_and_rates_fail_before_alsa(self):
        cases = [('0', 'BASE', 'BASE_SERIAL', 'EXP', 'EXP_SERIAL'),
                 ('192000', 'BASE', 'BASE_SERIAL', 'EXP', 'EXP_SERIAL'),
                 ('96000', 'BASE', '', 'EXP', 'EXP_SERIAL'),
                 ('96000', 'BASE', 'BASE_SERIAL', 'EXP', 'BASE_SERIAL'),
                 ('96000', 'BASE', 'A' * 65, 'EXP', 'EXP_SERIAL'),
                 ('96000', 'hw:BASE', 'BASE_SERIAL', 'EXP', 'EXP_SERIAL'),
                 ('96000', 'BASE', 'BASE_SERIAL', 'BASE', 'EXP_SERIAL')]
        for values in cases:
            with self.subTest(values=values):
                result = self.invoke(['--experimental', *values])
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(json.loads(result.stdout),
                                 {'ok': False, 'error': -errno.EINVAL,
                                  'change_attempted': False, 'recovered': None})
                self.assertNotIn('ALSA lib', result.stderr)

    @unittest.skipUnless(os.geteuid() == 0, 'Root rejection is exercised when running under root/fakeroot.')
    def test_root_cannot_start_a_device_transaction(self):
        result = self.invoke(['--experimental', '96000', 'BASE', 'BASE_SERIAL', 'EXP', 'EXP_SERIAL'])
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertIn('regular device user', result.stderr)


if __name__ == '__main__':
    unittest.main()
