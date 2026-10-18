# SPDX-License-Identifier: GPL-2.0-only
"""GTK4/WebKitGTK host for the same authenticated local device control panel."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import sys
import threading

from .application import Application
from .navigation import local_document, local_download
from .server import LocalServer
from .window_controls import WindowControls


def gtk_modules():
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('WebKit', '6.0')
    from gi.repository import Gio, GLib, Gtk, WebKit
    if Gtk.get_minor_version() < 10:
        raise ValueError('GTK 4.10 or later is required for native file dialogs.')
    return Gio, GLib, Gtk, WebKit


class Desktop:
    def __init__(self, application, server):
        self.Gio, self.GLib, self.Gtk, self.WebKit = gtk_modules()
        self.device = application
        self.server = server
        self.window = None
        self.view = None
        self.window_controls = None
        self._downloads = {}
        self._server_thread = None
        self._signal_sources = []
        self.app = self.Gtk.Application(application_id='org.octa_capture.ControlPanel',
                                        flags=self.Gio.ApplicationFlags.NON_UNIQUE)
        self.app.connect('activate', self.activate)

    def activate(self, app):
        if self.window:
            self.window.present()
            return
        self.window = self.Gtk.ApplicationWindow(application=app, title='OCTA-CAPTURE')
        self.window.set_default_size(978, 429)
        self.window.set_resizable(False)
        self.window_controls = WindowControls(self.window, self.device.data_dir, self.GLib)
        session = self.WebKit.NetworkSession.new_ephemeral()
        session.set_proxy_settings(self.WebKit.NetworkProxyMode.NO_PROXY, None)
        session.connect('download-started', self.download_started)
        manager = self.WebKit.UserContentManager()
        manager.connect('script-message-with-reply-received::octaWindow', self.window_message)
        if not manager.register_script_message_handler_with_reply('octaWindow', None):
            raise RuntimeError('Could not register the window-control bridge.')
        self.view = self.WebKit.WebView(network_session=session, user_content_manager=manager)
        self.view.set_size_request(978, 429)
        settings = self.view.get_settings()
        settings.set_enable_developer_extras(False)
        settings.set_javascript_can_open_windows_automatically(False)
        settings.set_allow_file_access_from_file_urls(False)
        settings.set_allow_universal_access_from_file_urls(False)
        settings.set_enable_webgl(False)
        settings.set_enable_media(False)
        # The small 2D control panel must also render without a GPU in WSL/Xvfb.
        settings.set_hardware_acceleration_policy(self.WebKit.HardwareAccelerationPolicy.NEVER)
        self.view.connect('decide-policy', self.decide_policy)
        self.view.connect('permission-request', self.permission_request)
        self.view.connect('create', lambda *_args: None)
        self.view.connect('close', lambda *_args: self.window.close())
        self.view.connect('context-menu', lambda *_args: True)
        self.window.set_child(self.view)
        self.window.present()
        # The capability never enters argv, browser history or persistent storage.
        self.view.load_uri(f'{self.server.origin}/#token={self.server.token}')

    def window_message(self, _manager, value, reply):
        return self.window_controls.message(self.view, self.server.origin, value, reply)

    def decide_policy(self, _view, decision, kind):
        if kind == self.WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if local_download(uri, self.server.origin):
                decision.download()
                return True
            allowed = local_document(uri, self.server.origin)
        elif kind == self.WebKit.PolicyDecisionType.RESPONSE:
            uri = decision.get_request().get_uri()
            if local_download(uri, self.server.origin):
                decision.download()
                return True
            allowed = local_document(uri, self.server.origin)
        else:
            allowed = False
        decision.use() if allowed else decision.ignore()
        return True

    @staticmethod
    def permission_request(_view, request):
        request.deny()
        return True

    def download_started(self, _session, download):
        if not local_download(download.get_request().get_uri(), self.server.origin):
            download.cancel()
            return
        download.connect('decide-destination', self.download_destination)
        download.connect('finished', self.download_finished)
        download.connect('failed', self.download_failed)

    def download_destination(self, download, _suggestion):
        chooser = self.Gtk.FileDialog(title='Save OCTA-CAPTURE settings')
        chooser.set_initial_name('OCTA-CAPTURE.octa')
        cancellable = self.Gio.Cancellable()
        self._downloads[download] = cancellable

        def selected(dialog, result, _data):
            try:
                file = dialog.save_finish(result)
                if not file or not file.get_path():
                    download.cancel()
                    return
                # The native Save dialog asks before replacing an existing file.
                download.set_allow_overwrite(True)
                download.set_destination(file.get_path())
            except self.GLib.Error:
                download.cancel()
        chooser.save(self.window, cancellable, selected, None)
        return True

    def download_finished(self, download):
        self._downloads.pop(download, None)

    def download_failed(self, download, error):
        self._downloads.pop(download, None)
        print(f'Settings download ended: {error.message}', file=sys.stderr)

    def quit(self):
        self.app.quit()
        return False

    def run(self):
        self._server_thread = threading.Thread(target=self.server.serve_forever,
                                               kwargs={'poll_interval': 0.1}, daemon=True)
        self._server_thread.start()
        for signum in (signal.SIGINT, signal.SIGTERM):
            self._signal_sources.append(self.GLib.unix_signal_add(self.GLib.PRIORITY_DEFAULT, signum, self.quit))
        try:
            return self.app.run([])
        finally:
            for source in self._signal_sources:
                if self.GLib.MainContext.default().find_source_by_id(source):
                    self.GLib.source_remove(source)
            for download, cancellable in list(self._downloads.items()):
                cancellable.cancel()
                download.cancel()
            if self.view:
                self.view.stop_loading()
            if self.window_controls:
                self.window_controls.close()
            if self.window:
                self.window.destroy()
            self.server.shutdown()
            self._server_thread.join()
            self.device.close()
            self.server.server_close()


def main(*, default_bin_dir=None):
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=default_bin_dir or root / 'build/release')
    parser.add_argument('--midi-port', default='hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1')
    parser.add_argument('--data-dir', type=Path,
                        default=Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'octa-capture')
    args = parser.parse_args()
    try:
        gtk_modules()
    except (ImportError, ValueError) as error:
        parser.exit(1, f'GTK4/WebKitGTK is unavailable: {error}\n'
                    'Debian packages: python3-gi gir1.2-gtk-4.0 gir1.2-webkit-6.0\n')
    application = Application(args.bin_dir, args.data_dir, args.midi_port)
    server = LocalServer(application, port=0)
    return Desktop(application, server).run()
