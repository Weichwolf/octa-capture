#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build a source-only Debian DKMS package from a supported Linux ALSA snapshot."""

import argparse
import gzip
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def kernel_version(source):
    values = {}
    for line in (source / 'Makefile').read_text().splitlines():
        name, separator, value = line.partition('=')
        if separator and name.strip() in ('VERSION', 'PATCHLEVEL', 'SUBLEVEL', 'EXTRAVERSION'):
            values[name.strip()] = value.strip()
    version = '.'.join(values[name] for name in ('VERSION', 'PATCHLEVEL', 'SUBLEVEL')) + values.get('EXTRAVERSION', '')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)?', version):
        raise ValueError('Expected a stable supported kernel version.')
    series = '.'.join(version.split('.')[:2])
    if not (ROOT / 'kernel' / f'linux-{series}.patch').is_file():
        raise ValueError(f'Unsupported kernel series: {series}')
    return version, series


def write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


def compressed_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as output, gzip.GzipFile(filename='', mode='wb', fileobj=output, mtime=0) as compressed:
        compressed.write(text.encode())


def template(name, values):
    text = (ROOT / 'packaging' / name).read_text()
    for key, value in values.items():
        text = text.replace(f'@{key}@', value)
    return text


def package_modes(stage):
    for path in (stage, *stage.rglob('*')):
        if path.is_symlink():
            continue
        executable = bool(path.stat().st_mode & 0o111)
        path.chmod(0o755 if path.is_dir() or executable else 0o644)


def prepare(stage, source, version, series, baseline):
    module = stage / 'usr/src' / f'snd-octa-capture-{version}'
    subprocess.run([str(ROOT / 'tools/prepare-alsa.sh'), str(source), str(module)], check=True)
    shutil.copyfile(ROOT / 'tools/dkms-build.sh', module / 'dkms-build.sh')
    (module / 'dkms-build.sh').chmod(0o755)
    shutil.copyfile(ROOT / 'LICENSE', module / 'COPYING')
    write(module / 'BUILD-SERIES', series + '\n')
    write(module / 'SOURCE-KERNEL', baseline + '\n')
    values = {'VERSION': version, 'SERIES_REGEX': series.replace('.', '[.]')}
    write(module / 'dkms.conf', template('dkms.conf.in', values))
    write(stage / 'DEBIAN/postinst', template('dkms-postinst.in', values), 0o755)
    write(stage / 'DEBIAN/prerm', template('dkms-prerm.in', values), 0o755)
    write(stage / 'DEBIAN/postrm', template('dkms-postrm.in', values), 0o755)
    compressed_text(stage / 'usr/share/man/man8/octa-capture-activate.8.gz',
                    template('man/octa-capture-activate.8.in', {'PROJECT_VERSION': version}))
    (stage / 'usr/share/metainfo').mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / 'packaging/org.octa_capture.driver.metainfo.xml',
                    stage / 'usr/share/metainfo/org.octa_capture.driver.metainfo.xml')
    write(stage / 'etc/modprobe.d/octa-capture.conf',
          '# Prefer the dedicated OCTA-CAPTURE driver before generic USB audio.\n'
          'softdep snd-usb-audio pre: snd-octa-capture\n')
    write(stage / 'DEBIAN/conffiles', '/etc/modprobe.d/octa-capture.conf\n')
    rules = stage / 'usr/lib/udev/rules.d/70-octa-capture.rules'
    rules.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / 'packaging/70-octa-capture.rules', rules)
    service = stage / 'usr/lib/systemd/system/octa-capture-activate@.service'
    service.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / 'packaging/octa-capture-activate@.service', service)
    activation = stage / 'usr/sbin/octa-capture-activate'
    activation.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / 'tools/activate-module.sh', activation)
    activation.chmod(0o755)
    maintainer = next(line.removeprefix('Maintainer: ') for line in (ROOT / 'debian/control').read_text().splitlines()
                      if line.startswith('Maintainer: '))
    write(stage / 'DEBIAN/control', f'''Package: octa-capture-dkms
Version: {version}
Architecture: all
Maintainer: {maintainer}
Section: kernel
Priority: optional
Depends: dkms (>= 3.0), kmod, make, gcc
Recommends: linux-headers-amd64
Homepage: https://github.com/Weichwolf/octa-capture
Description: Standalone OCTA-CAPTURE USB audio and MIDI module
 Builds snd-octa-capture for kernel series {series} using DKMS.
 Includes a private ALSA source snapshot from Linux {baseline}.
 No kernel patching or kernel rebuild is required.
''')
    documentation = stage / 'usr/share/doc/octa-capture-dkms'
    documentation.mkdir(parents=True)
    shutil.copyfile(ROOT / 'debian/copyright', documentation / 'copyright')
    release = next(line for line in (ROOT / 'debian/changelog').read_text().splitlines() if line.startswith(' -- '))
    date = release.rsplit('  ', 1)[1]
    compressed_text(documentation / 'changelog.gz', f'''octa-capture-dkms ({version}) unstable; urgency=medium

  * Package the standalone module with its Linux {baseline} ALSA source snapshot.

 -- {maintainer}  {date}
''')
    write(documentation / 'README', f'''OCTA-CAPTURE standalone DKMS module

ALSA source baseline: Linux {baseline}. Supported header series: {series}.
The complete module source is installed in /usr/src/snd-octa-capture-{version}.
Original Linux/ALSA copyright and license notices remain in each source file.
DKMS rebuilds this external module when matching-series kernel headers change.
Builds use the kernel compiler, W=1 and -Werror. Other kernel series are skipped.
Close audio/control applications, then run sudo octa-capture-activate.
This loads the module and binds only 0582:0120/0121; other USB audio devices remain bound.
octa-capture-activate --check is read-only and works without root.
On systemd systems, a device-specific udev rule activates the standalone driver
after hotplug if the generic USB audio driver claims OCTA-CAPTURE first.
GUI/device tools are provided by the separate octa-capture package.
''')
    package_modes(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kernel_source', type=Path)
    parser.add_argument('output_directory', type=Path)
    args = parser.parse_args()
    try:
        source = args.kernel_source.resolve(strict=True)
        baseline, series = kernel_version(source)
        project = (ROOT / 'VERSION').read_text().strip()
        if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', project):
            raise ValueError('Invalid project VERSION.')
        version = f'{project}+linux.{baseline}'
        output = args.output_directory.resolve()
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.dkms-package-', dir=output) as temporary:
            stage = Path(temporary) / 'root'
            prepare(stage, source, version, series, baseline)
            package = output / f'octa-capture-dkms_{version}_all.deb'
            subprocess.run(['dpkg-deb', '--root-owner-group', '--build', str(stage), str(package)], check=True)
        print(package)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Could not create DKMS package: {error}\n')


if __name__ == '__main__':
    main()
