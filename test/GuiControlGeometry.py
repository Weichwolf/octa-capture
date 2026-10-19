# SPDX-License-Identifier: GPL-2.0-only
"""Reject incompatible or incomplete original-window layout observations offline."""

import copy
import json
from pathlib import Path
import runpy
import unittest

ROOT = Path(__file__).resolve().parent.parent
INSPECTOR = runpy.run_path(str(ROOT / 'tools/inspect-windows-layout.py'))
REFERENCE = json.loads((ROOT / 'test/data/control-geometry.json').read_text())


class Geometry(unittest.TestCase):
    def observation(self, view):
        result = {key: REFERENCE[key] for key in ('source_sha256', 'client_size', 'menu_height', 'dpi')}
        result['controls'] = [{'id': item['id'], 'rect': [item['rect'][0], item['rect'][1] - 19, *item['rect'][2:]]}
                              for item in REFERENCE['views'][view]]
        return result

    def test_complete_views_include_the_native_menu_translation(self):
        for view, count in (('preamp', 128), ('mixer', 174)):
            self.assertEqual(INSPECTOR['compare'](self.observation(view), REFERENCE), {'view': view, 'matched': count})

    def test_other_binary_dpi_client_size_or_menu_is_not_equivalent(self):
        for key, value in (('source_sha256', '0' * 64), ('dpi', 144), ('client_size', [978, 429]), ('menu_height', 20)):
            observed = self.observation('preamp')
            observed[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'metadata differs'):
                INSPECTOR['compare'](observed, REFERENCE)

    def test_missing_or_duplicate_controls_cannot_pass(self):
        for duplicate in (False, True):
            observed = self.observation('mixer')
            removed = observed['controls'].pop(0)
            if duplicate:
                observed['controls'].extend([removed, copy.deepcopy(removed)])
            with self.subTest(duplicate=duplicate), self.assertRaisesRegex(ValueError, 'one visible original control'):
                INSPECTOR['compare'](observed, REFERENCE)

    def test_every_coordinate_and_dimension_is_compared(self):
        for index in range(4):
            observed = self.observation('preamp')
            observed['controls'][0]['rect'][index] += 1
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'bounds differ'):
                INSPECTOR['compare'](observed, REFERENCE)

    def test_hidden_main_views_are_not_assumed(self):
        for controls in ([], [{'id': 775}, {'id': 521}]):
            observed = self.observation('preamp')
            observed['controls'] = controls
            with self.subTest(controls=controls), self.assertRaisesRegex(ValueError, 'one visible original main view'):
                INSPECTOR['compare'](observed, REFERENCE)


if __name__ == '__main__':
    unittest.main()
