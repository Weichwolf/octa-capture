#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build the Debian 13 package in the same unprivileged sbuild workflow as btop."""

import argparse
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def source_package(output):
    version = (ROOT / 'VERSION').read_text().strip()
    source = output / f'octa-capture-{version}'
    source.mkdir()
    paths = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                                    cwd=ROOT).decode().split('\0')
    for name in filter(None, paths):
        path = ROOT / name
        if not path.is_file():
            continue
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    epoch = int(subprocess.check_output(['dpkg-parsechangelog', '-S', 'Timestamp'], cwd=source))
    def metadata(item):
        item.uid = item.gid = 0
        item.uname = item.gname = 'root'
        item.mtime = min(item.mtime, epoch)
        return item
    archive = output / f'octa-capture_{version}.orig.tar.xz'
    with tarfile.open(archive, 'w:xz') as bundle:
        for path in sorted(source.iterdir()):
            if path.name != 'debian':
                bundle.add(path, arcname=f'octa-capture-{version}/{path.name}', filter=metadata)
    subprocess.run(['dpkg-source', '--build', str(source)], cwd=output, check=True)
    return next(output.glob('*.dsc'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chroot', type=Path, default=Path.home() / '.cache/sbuild/trixie-amd64.tar')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/debian-trixie')
    parser.add_argument('--jobs', type=int, choices=range(1, 17), default=2)
    args = parser.parse_args()
    args.chroot.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    dsc = source_package(output)
    subprocess.run(['sbuild', '--chroot-mode=unshare', '--dist=trixie', '--arch=amd64',
                    f'--jobs={args.jobs}', '--no-run-lintian', f'--chroot={args.chroot.resolve()}', str(dsc)],
                   cwd=output, check=True)
    print(output)


if __name__ == '__main__':
    main()
