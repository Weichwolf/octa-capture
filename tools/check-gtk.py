#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the GTK/WebKit panel on X11, including its actual native Save dialog."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
location_parser = argparse.ArgumentParser(add_help=False)
location_parser.add_argument('--installed-prefix', type=Path)
installed_prefix = location_parser.parse_known_args()[0].installed_prefix
application_root = installed_prefix.resolve() / 'libexec/octa-capture' if installed_prefix else ROOT
sys.path.insert(0, str(application_root))
from gui.application import Application
from gui.desktop import Desktop
from gui.server import LocalServer


class Check(Desktop):
    def __init__(self, application, server, directory, write):
        super().__init__(application, server)
        self.directory = directory
        self.write = write
        self.errors = []
        self.result = None
        self.saved = directory / 'GTK-settings.octa'
        self.phase = 'loading'
        self.deadline = time.monotonic() + 40
        self.pending = False
        self.loads = 0

    def fail(self, error):
        self.errors.append(str(error))
        self.quit()

    def activate(self, app):
        super().activate(app)
        self.view.connect('load-changed', self.loaded)
        self.view.connect('web-process-terminated', lambda _view, reason: self.fail(f'Web process terminated: {reason}'))
        self.GLib.timeout_add(100, self.tick)

    def loaded(self, _view, event):
        if event == self.WebKit.LoadEvent.FINISHED:
            self.loads += 1
            print(f'WebKit document finished: {self.loads}', flush=True)
        if event != self.WebKit.LoadEvent.FINISHED or self.phase != 'loading':
            return
        self.phase = 'suite'
        source = (ROOT / 'test/GtkPanel.mjs').read_text().replace('export async function', 'async function')
        source += f'\nreturn await checkPanel({str(self.write).lower()});'

        def finished(view, result, _data):
            try:
                value = view.call_async_javascript_function_finish(result)
                self.result = json.loads(value.to_json(0))
                self.phase = 'download'
                window = subprocess.run(['xdotool', 'search', '--onlyvisible', '--pid', str(os.getpid())],
                                        capture_output=True, text=True, check=True, timeout=3).stdout.splitlines()[0]
                subprocess.run(['xdotool', 'windowfocus', window, 'key', 'ctrl+s'], check=True, timeout=3)
            except Exception as error:
                self.fail(error)
        self.view.call_async_javascript_function(source, -1, None, None, None, None, finished, None)

    def tick(self):
        try:
            return self._tick()
        except Exception as error:
            self.fail(error)
            return False

    def _tick(self):
        if time.monotonic() > self.deadline:
            self.fail(f'GTK check timed out in {self.phase}.')
            return False
        if self.phase == 'download':
            for window in self.Gtk.Window.list_toplevels():
                if window.get_visible() and window.get_title() == 'Save OCTA-CAPTURE settings':
                    identifier = subprocess.run(['xdotool', 'search', '--all', '--onlyvisible', '--pid',
                                                 str(os.getpid()), '--name', '^Save OCTA-CAPTURE settings$'],
                                                capture_output=True, text=True, check=True, timeout=3).stdout.splitlines()[0]
                    subprocess.run(['xdotool', 'windowfocus', identifier, 'key', 'ctrl+l', 'ctrl+a'], check=True, timeout=3)
                    subprocess.run(['xdotool', 'type', '--clearmodifiers', '--delay', '1', '--', str(self.saved)],
                                   check=True, timeout=5)
                    subprocess.run(['xdotool', 'key', 'Return'], check=True, timeout=3)
                    self.phase = 'saving'
                    break
        elif self.phase == 'saving' and self.saved.exists() and self.saved.stat().st_size == 1639:
            if not self.saved.read_bytes().startswith(b'OCTA-CAPTURE Control Panel Data\0Ver100\0'):
                self.fail('Invalid native settings download.')
                return False
            self.phase = 'saved'

            def inspect(view, result, _data):
                try:
                    value = json.loads(view.evaluate_javascript_finish(result).to_json(0))
                    if not value['connected'] or not value['mixer'] or self.loads != 1:
                        raise RuntimeError(f'Desktop lost its live view after Save: {value}; loads={self.loads}')
                    subprocess.run(['import', '-window', 'root', str(self.directory / 'gtk.png')], check=True, timeout=5)
                    self.phase = 'navigation'
                    self.view.load_uri('https://example.invalid/should-be-blocked')
                except Exception as error:
                    self.fail(error)
            self.view.evaluate_javascript('({connected:document.querySelector("#connection-message").hidden,'
                                          'mixer:!document.querySelector("#mixer-view").hidden})',
                                          -1, None, None, None, inspect, None)
        elif self.phase == 'navigation' and not self.pending:
            self.pending = True
            self.GLib.timeout_add(500, self.check_navigation)
        return not self.errors

    def check_navigation(self):
        if not self.view.get_uri().startswith(self.server.origin + '/'):
            self.fail('External navigation was not blocked.')
        else:
            self.phase = 'complete'
            script = "document.querySelector('[data-menu=driver]').click();" \
                     "[...document.querySelectorAll('#menus button')].find(node=>node.textContent.startsWith('Exit')).click();"
            self.view.evaluate_javascript(script, -1, None, None, None, None, None)
            self.GLib.timeout_add_seconds(3, lambda: self.fail('Driver Exit did not close the desktop window.'))
        return False


def run(command):
    return subprocess.run(list(map(str, command)), capture_output=True, check=True, timeout=20).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installed-prefix', type=Path, help='test installed Python modules, assets and executables')
    parser.add_argument('--bin-dir', type=Path,
                        default=installed_prefix.resolve() / 'bin' if installed_prefix else ROOT / 'build/release')
    parser.add_argument('--write', action='store_true', help='change one sensitivity value and restore the full raw state')
    args = parser.parse_args()
    parent = ROOT / 'build/gui'
    parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='gtk-check-', dir=parent))
    application = Application(args.bin_dir, directory / 'recovery')
    before = application.snapshot()['state']
    body = bytes([1, 0, 0, 0]) + bytes.fromhex(before['payload_hex'])
    recovery = directory / 'original.syx'
    recovery.write_bytes(bytes.fromhex('f0411000004d12') + body + bytes([-sum(body) % 128, 247]))
    recovery.chmod(0o600)
    server = LocalServer(application, port=0)
    try:
        checker = Check(application, server, directory, args.write)
        status = checker.run()
        if status or checker.errors or checker.phase != 'complete':
            raise RuntimeError(f'GTK check failed: {checker.errors}; phase={checker.phase}; status={status}')
        run([args.bin_dir / 'octa-preset', 'inspect', checker.saved])
    finally:
        application.close()
        server.server_close()
        if args.write:
            run([args.bin_dir / 'octa-preset', 'restore', recovery])
        after = json.loads(run([args.bin_dir / 'octa-state']))
        if after != before:
            raise RuntimeError(f'Final state mismatch. Recovery snapshot: {recovery}')
    print(f'PASS GTK/WebKit live panel, controls, dialogs, native Save, navigation policy and raw preservation: {directory}')


if __name__ == '__main__':
    main()
