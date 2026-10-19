# SPDX-License-Identifier: GPL-2.0-only
"""Own ALSA shared-channel definitions and native PulseAudio stereo profiles."""

import hashlib
import os
from pathlib import Path
import re
import tempfile

RATES = (44100, 48000, 96000, 192000)
PROFILE = 'octa-stereo-pairs'


def channel_counts(rate):
    if type(rate) is not int or rate not in RATES:
        raise ValueError('Unsupported sample rate.')
    return (4, 4) if rate == 192000 else (10, 12)


def configuration(card_id, rate):
    if not re.fullmatch(r'[A-Za-z0-9_]{1,15}', card_id):
        raise ValueError('Invalid ALSA card identifier.')
    playback, recording = channel_counts(rate)
    alsa = ['# SPDX-License-Identifier: GPL-2.0-only\n']
    profile = ['# SPDX-License-Identifier: GPL-2.0-only\n[General]\nauto-profiles = no\n']
    mappings = {}
    for direction, count, plugin in (('playback', playback, 'dshare'), ('recording', recording, 'dsnoop')):
        mappings[direction] = []
        key = int.from_bytes(hashlib.sha256(f'octa:{card_id}:{rate}:{direction}'.encode()).digest()[:4], 'little')
        key = (key & 0x7fffffff) or 1
        for channel in range(0, count, 2):
            pcm = f'octa_{card_id}_{direction}_{channel + 1}_{channel + 2}'
            alsa.append(f'pcm.{pcm} {{\n type {plugin}\n ipc_key {key}\n ipc_key_add_uid true\n'
                        f' slave {{ pcm "hw:CARD={card_id},DEV=0" format S24_3LE rate {rate}'
                        f' channels {count} period_size 1024 buffer_size 8192 }}\n'
                        f' bindings {{ 0 {channel} 1 {channel + 1} }}\n}}\n')
            mapping = f'{direction}-{channel + 1}-{channel + 2}'
            mappings[direction].append(mapping)
            profile.append(f'\n[Mapping {mapping}]\ndescription = {direction.title()} {channel + 1}-{channel + 2}\n'
                           f'device-strings = {pcm}\nchannel-map = front-left,front-right\n'
                           f'direction = {"output" if direction == "playback" else "input"}\nexact-channels = yes\n')
    profile.append(f'\n[Profile {PROFILE}]\ndescription = Stereo Pairs\n'
                   f'output-mappings = {" ".join(mappings["playback"])}\n'
                   f'input-mappings = {" ".join(mappings["recording"])}\npriority = 100\nskip-probe = yes\n')
    return ''.join(alsa).encode(), ''.join(profile).encode()


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.', delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class Files:
    def __init__(self, card_id, root=None):
        self.root = root or Path.home() / '.local/state/octa-capture/pulse' / card_id
        self.alsa = self.root / 'alsa.conf'
        self.profile = self.root / 'profile.conf'
        self.preference = self.root / 'rate'
        self.user = Path.home() / '.asoundrc'
        self.original = {}

    def install(self, card_id, rate):
        paths = (self.alsa, self.profile, self.preference, self.user)
        self.original = {path: path.read_bytes() if path.exists() else None for path in paths}
        alsa, profile = configuration(card_id, rate)
        include = f'<{self.alsa}>\n'.encode()
        user = self.original[self.user] or b''
        if include not in user.splitlines(keepends=True):
            user = user.rstrip(b'\n') + b'\n' + include
        for path, data in ((self.alsa, alsa), (self.profile, profile), (self.preference, str(rate).encode()),
                           (self.user, user)):
            atomic_write(path, data)

    def restore(self):
        for path, data in self.original.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, data)
