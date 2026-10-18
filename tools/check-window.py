#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check the real GTK window bridge with or without an EWMH window manager.

Run on a private X server, e.g. xvfb-run -a dbus-run-session -- python3
tools/check-window.py --xfwm. No audio device is opened; two process launches
also verify restoration of the confirmed persistent preference.
"""

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
from gui.preferences import WindowPreferences
from gui.server import LocalServer


def check_empty_display():
    from Xlib import Xatom, display
    connection = display.Display()
    root = connection.screen().root
    owner = root.get_property(connection.intern_atom('_NET_SUPPORTING_WM_CHECK'), Xatom.WINDOW, 0, 1)
    connection.close()
    if owner is not None and len(owner.value):
        raise ValueError('An existing window manager was found; use a private xvfb-run display.')


def wait_for_manager(manager):
    from Xlib import Xatom, display
    connection = display.Display()
    try:
        root = connection.screen().root
        supported = connection.intern_atom('_NET_SUPPORTED')
        above = connection.intern_atom('_NET_WM_STATE_ABOVE')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if manager.poll() is not None:
                raise RuntimeError('The private window manager exited during startup.')
            value = root.get_property(supported, Xatom.ATOM, 0, 4096)
            if value is not None and value.format == 32 and not value.bytes_after and above in value.value:
                return
            time.sleep(0.02)
        raise RuntimeError('The private window manager did not advertise Always on top.')
    finally:
        connection.close()


class Check(Desktop):
    def __init__(self, directory, supported, restored):
        # A deliberately nonexistent bridge executable rules out device access.
        application = Application(directory / 'missing-bin', directory)
        super().__init__(application, LocalServer(application, port=0))
        self.supported = supported
        self.restored = restored
        self.result = None
        self.errors = []

    def activate(self, app):
        super().activate(app)
        self.view.connect('load-changed', self.loaded)
        self.GLib.timeout_add_seconds(30, self.expired)

    def expired(self):
        self.errors.append('Window integration check timed out.')
        return self.quit()

    def loaded(self, _view, event):
        if event != self.WebKit.LoadEvent.FINISHED:
            return
        source = (ROOT / 'test/WindowPanel.mjs').read_text().replace('export async function', 'async function')
        source += f'\nreturn await checkWindow({str(self.supported).lower()}, {str(self.restored).lower()});'

        def finished(view, result, _data):
            try:
                self.result = json.loads(view.call_async_javascript_function_finish(result).to_json(0))
                actual = self.window_controls.native.state()
                if actual != self.result:
                    raise RuntimeError(f'Window manager disagrees with the panel: {actual}, {self.result}')
                surface = self.window.get_surface()
                external = subprocess.check_output(['xprop', '-id', str(surface.get_xid()), '_NET_WM_STATE'],
                                                   text=True, timeout=3)
                if ('_NET_WM_STATE_ABOVE' in external) != self.result['above']:
                    raise RuntimeError(f'Independent xprop read disagrees with the bridge: {external}')
                if WindowPreferences(self.device.data_dir).document()['always_on_top'] != self.supported:
                    raise RuntimeError('Persistent window preference disagrees with confirmed state.')
            except Exception as error:
                self.errors.append(str(error))
            self.quit()
        self.view.call_async_javascript_function(source, -1, None, None, None, None, finished, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xfwm', action='store_true', help='start xfwm4 on this private X server')
    parser.add_argument('--installed-prefix', type=Path, help='test installed Python modules and web assets')
    parser.add_argument('--child', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--restored', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        check = Check(args.child, args.xfwm, args.restored)
        if check.run() or check.errors or check.result is None:
            raise RuntimeError(f'Window check failed: {check.errors}')
        print(f'PASS native window bridge, menu and persistent state: {check.result}', flush=True)
        return
    if not os.environ.get('DISPLAY'):
        parser.error('Run under xvfb-run on a private display.')
    try:
        check_empty_display()
    except ValueError as error:
        parser.error(str(error))
    manager = None
    with tempfile.TemporaryDirectory(prefix='octa-window-') as name:
        directory = Path(name)
        try:
            if args.xfwm:
                manager = subprocess.Popen(['xfwm4', '--compositor=off', '--sm-client-disable'],
                                           stdout=subprocess.DEVNULL)
                wait_for_manager(manager)
            command = [sys.executable, __file__, '--child', name] + (['--xfwm'] if args.xfwm else [])
            if args.installed_prefix:
                command += ['--installed-prefix', str(args.installed_prefix.resolve())]
            subprocess.run(command, check=True, timeout=40)
            if args.xfwm:
                subprocess.run(command + ['--restored'], check=True, timeout=40)
        finally:
            if manager is not None:
                manager.terminate()
                manager.wait(timeout=5)


if __name__ == '__main__':
    main()
