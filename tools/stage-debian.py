#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Stage the native Debian package's private ALSA snapshot without building a kernel."""

import argparse
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DKMS = runpy.run_path(str(ROOT / 'tools/package-dkms.py'))


def stage(archive, target):
    version = (ROOT / 'VERSION').read_text().strip()
    with tempfile.TemporaryDirectory(prefix='octa-debian-') as directory:
        temporary = Path(directory)
        subprocess.run(['tar', '--extract', '--xz', '--file', str(archive), '--directory', directory,
                        '--wildcards', 'linux-source-6.12/Makefile', 'linux-source-6.12/sound/usb/*'], check=True)
        source = temporary / 'linux-source-6.12'
        baseline, series = DKMS['kernel_version'](source)
        prepared = temporary / 'package'
        DKMS['prepare'](prepared, source, version, series, baseline)
        for name in ('usr', 'etc'):
            shutil.copytree(prepared / name, target / name, dirs_exist_ok=True)
        shutil.rmtree(target / 'usr/share/doc/octa-capture-dkms')
        shutil.copyfile(ROOT / 'kernel/README.md', target / 'usr/share/doc/octa-capture/README.driver.md')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('linux_source_archive', type=Path)
    parser.add_argument('package_root', type=Path)
    args = parser.parse_args()
    stage(args.linux_source_archive.resolve(strict=True), args.package_root.resolve(strict=True))


if __name__ == '__main__':
    main()
