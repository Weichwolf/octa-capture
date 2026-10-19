#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Read visible control bounds from the original Windows panel without changing it."""

import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import sys

REFERENCE_SHA256 = '634c140bc4e49b0d65c70d5156be19573ad3d440ac5ea24e24d3ad59974da406'


def compare(observed, reference):
    for key in ('source_sha256', 'client_size', 'menu_height', 'dpi'):
        if observed.get(key) != reference[key]:
            raise ValueError(f'Original layout metadata differs: {key}.')
    identifiers = {control['id'] for control in observed['controls']}
    if (775 in identifiers) == (521 in identifiers):
        raise ValueError('Expected one visible original main view.')
    view = 'preamp' if 775 in identifiers else 'mixer'
    for item in reference['views'][view]:
        matches = [control for control in observed['controls'] if control['id'] == item['id']]
        if len(matches) != 1:
            raise ValueError(f'Expected one visible original control: {view}/{item["id"]}.')
        x, y, width, height = matches[0]['rect']
        if [x, y + observed['menu_height'], width, height] != item['rect']:
            raise ValueError(f'Original control bounds differ: {view}/{item["id"]}.')
    return {'view': view, 'matched': len(reference['views'][view])}


def capture():
    if sys.platform != 'win32':
        raise OSError('Run this inspector with Windows Python alongside the original control panel.')
    from ctypes import wintypes as w
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    callback = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    declarations = {
        'EnumWindows': (w.BOOL, [callback, w.LPARAM]),
        'EnumChildWindows': (w.BOOL, [w.HWND, callback, w.LPARAM]),
        'IsWindowVisible': (w.BOOL, [w.HWND]),
        'GetWindowTextW': (ctypes.c_int, [w.HWND, w.LPWSTR, ctypes.c_int]),
        'GetClassNameW': (ctypes.c_int, [w.HWND, w.LPWSTR, ctypes.c_int]),
        'GetDlgCtrlID': (ctypes.c_int, [w.HWND]),
        'GetWindowRect': (w.BOOL, [w.HWND, ctypes.POINTER(w.RECT)]),
        'GetClientRect': (w.BOOL, [w.HWND, ctypes.POINTER(w.RECT)]),
        'ClientToScreen': (w.BOOL, [w.HWND, ctypes.POINTER(w.POINT)]),
        'GetWindowThreadProcessId': (w.DWORD, [w.HWND, ctypes.POINTER(w.DWORD)]),
        'GetSystemMetrics': (ctypes.c_int, [ctypes.c_int]),
        'GetDpiForWindow': (w.UINT, [w.HWND]),
    }
    for name, (result, arguments) in declarations.items():
        function = getattr(user, name)
        function.restype, function.argtypes = result, arguments
    kernel.OpenProcess.restype, kernel.OpenProcess.argtypes = w.HANDLE, [w.DWORD, w.BOOL, w.DWORD]
    kernel.CloseHandle.restype, kernel.CloseHandle.argtypes = w.BOOL, [w.HANDLE]
    kernel.QueryFullProcessImageNameW.restype = w.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]

    def text(window, method='GetWindowTextW'):
        buffer = ctypes.create_unicode_buffer(1024)
        getattr(user, method)(window, buffer, len(buffer))
        return buffer.value

    windows = []

    @callback
    def find(window, _data):
        if user.IsWindowVisible(window) and text(window) == 'OCTA-CAPTURE Control Panel':
            windows.append(window)
        return True
    if not user.EnumWindows(find, 0) or len(windows) != 1:
        raise OSError('Expected one visible original OCTA-CAPTURE control panel.')
    window, pid = windows[0], w.DWORD()
    if not user.GetWindowThreadProcessId(window, ctypes.byref(pid)):
        raise ctypes.WinError(ctypes.get_last_error())
    process = kernel.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION.
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        path, length = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
        if not kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        digest = hashlib.sha256(Path(path.value).read_bytes()).hexdigest()
        if digest != REFERENCE_SHA256:
            raise ValueError('Unexpected original control-panel executable hash.')
    finally:
        kernel.CloseHandle(process)
    origin, client = w.POINT(), w.RECT()
    if not user.ClientToScreen(window, ctypes.byref(origin)) or not user.GetClientRect(window, ctypes.byref(client)):
        raise ctypes.WinError(ctypes.get_last_error())
    controls, errors = [], []

    @callback
    def child(node, _data):
        if not user.IsWindowVisible(node):
            return True
        rect = w.RECT()
        if not user.GetWindowRect(node, ctypes.byref(rect)):
            errors.append(ctypes.get_last_error())
            return False
        controls.append({'id': user.GetDlgCtrlID(node), 'class': text(node, 'GetClassNameW'),
                         'text': text(node), 'rect': [rect.left - origin.x, rect.top - origin.y,
                                                   rect.right - rect.left, rect.bottom - rect.top]})
        return True
    user.EnumChildWindows(window, child, 0)
    if errors:
        raise ctypes.WinError(errors[0])
    return {'source_sha256': digest, 'client_size': [client.right, client.bottom],
            'menu_height': user.GetSystemMetrics(15), 'dpi': user.GetDpiForWindow(window),
            'controls': controls}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='save a private JSON observation instead of printing it')
    parser.add_argument('--compare', type=Path, help='compare the visible view with our numeric layout fixture')
    args = parser.parse_args()
    try:
        observation = capture()
        comparison = compare(observation, json.loads(args.compare.read_text(encoding='utf-8'))) if args.compare else None
        result = json.dumps(observation, indent=2) + '\n'
        if args.output:
            with args.output.open('x', encoding='utf-8') as output:
                output.write(result)
        elif comparison is None:
            print(result, end='')
        if comparison is not None:
            print(json.dumps(comparison))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
