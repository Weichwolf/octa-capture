#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check GTK/WebKit rate-dependent presentation using synthetic replies only."""

import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--installed-prefix', type=Path, help='check extracted/staged package modules and assets')
args = parser.parse_args()
application_root = args.installed_prefix.resolve() / 'libexec/octa-capture' if args.installed_prefix else ROOT
sys.path.insert(0, str(application_root))
from gui.application import Application
from gui.backend import DeviceError
from gui.desktop import Desktop
from gui.server import LocalServer


class Offline(Application):
    def snapshot(self):
        raise DeviceError('Offline presentation check: no device backend.')

    def meters(self):
        return {'meters': None}

    def devices(self):
        return {'devices': []}

    def change(self, _body):
        raise RuntimeError('Offline check must never send device controls.')

    def action(self, _body):
        raise RuntimeError('Offline check must never send device actions.')


class Check(Desktop):
    def __init__(self, directory):
        # Missing binaries provide another guard against hardware access.
        application = Offline(directory / 'missing-bin', directory)
        super().__init__(application, LocalServer(application, port=0))
        self.errors = []
        self.result = None
        self.started = False

    def activate(self, app):
        super().activate(app)
        self.view.connect('load-changed', self.loaded)
        self.view.connect('web-process-terminated', lambda _view, reason: self.fail(f'Web process terminated: {reason}'))
        self.GLib.timeout_add_seconds(25, lambda: self.fail('GTK rate check timed out.'))

    def fail(self, message):
        self.errors.append(str(message))
        return self.quit()

    def loaded(self, _view, event):
        if event != self.WebKit.LoadEvent.FINISHED or self.started:
            return
        self.started = True
        source = (ROOT / 'test/GuiSyntheticModel.mjs').read_text().replace('export function', 'function')
        source += (ROOT / 'test/GtkRatePanel.mjs').read_text().replace('export async function', 'async function')
        source += (ROOT / 'test/GuiControlGeometry.mjs').read_text().replace('export ', '')
        reference = json.loads((ROOT / 'test/data/control-geometry.json').read_text())
        source += f'\nreturn await checkRates(fixture(), {json.dumps(reference)}, checkControlGeometry);'

        def finished(view, result, _data):
            try:
                self.result = json.loads(view.call_async_javascript_function_finish(result).to_json(0))
            except Exception as error:
                self.errors.append(str(error))
            self.quit()
        self.view.call_async_javascript_function(source, -1, None, None, None, None, finished, None)


def main():
    with tempfile.TemporaryDirectory(prefix='octa-gtk-rates-') as name:
        check = Check(Path(name))
        if check.run() or check.errors or check.result is None:
            raise RuntimeError(f'GTK rate check failed: {check.errors}')
        print(f'PASS real GTK/WebKit rate presentation with synthetic replies: {check.result}')


if __name__ == '__main__':
    main()
