# SPDX-License-Identifier: GPL-2.0-only
"""Retire exact copies of the old per-user installation in favor of package files."""

from pathlib import Path
import shlex
import subprocess
import sys


def _backup(path):
    backup = path.with_name(path.name + '.before-package')
    index = 0
    while backup.exists() or backup.is_symlink():
        index += 1
        backup = path.with_name(path.name + f'.before-package.{index}')
    path.rename(backup)


def _launchers(home, system):
    custom_gui = False
    for name in ('octa-capture', 'octa-capture-http', 'octa-pulse-pairs'):
        old, new = home / '.local/bin' / name, system / 'bin' / name
        if old.is_file() and not old.is_symlink() and new.is_file() and old.read_bytes() == new.read_bytes():
            _backup(old)
        elif name == 'octa-capture' and old.exists():
            custom_gui = True
    name = 'org.octa_capture.ControlPanel.desktop'
    old, new = home / '.local/share/applications' / name, system / 'share/applications' / name
    if custom_gui or not old.is_file() or old.is_symlink() or not new.is_file():
        return
    text, current = old.read_text(), new.read_text()
    commands = [line for line in text.splitlines() if line.startswith('Exec=')]
    packaged = next((line for line in current.splitlines() if line.startswith('Exec=')), '')
    if (len(commands) == 1 and shlex.split(commands[0][5:]) == [str(home / '.local/bin/octa-capture')]
            and text.replace(commands[0], packaged, 1) == current):
        _backup(old)


def migrate_legacy_service(system=Path('/usr')):
    home = Path.home()
    unit = home / '.config/systemd/user/octa-pulse-pairs.service'
    if unit.is_file():
        if unit.is_symlink():
            return
        text = unit.read_text()
        command = next((line.removeprefix('ExecStart=') for line in text.splitlines() if line.startswith('ExecStart=')), '')
        expected = [sys.executable, str(home / '.local/bin/octa-pulse-pairs'), '--watch']
        if 'Description=OCTA-CAPTURE PulseAudio stereo pairs\n' not in text or shlex.split(command) != expected:
            return
        for arguments in (('stop', unit.name), ('disable', unit.name)):
            subprocess.run(['systemctl', '--user', *arguments], check=True, timeout=15)
        _backup(unit)
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True, timeout=15)
    _launchers(home, system)
