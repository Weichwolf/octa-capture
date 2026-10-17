#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail

if [[ $# != 2 ]]; then
    echo "Usage: $0 KERNEL_SOURCE OUTPUT_DIRECTORY" >&2
    exit 2
fi

source_dir=$(realpath -e -- "$1")
output_dir=$(realpath -m -- "$2")
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

if [[ -e $output_dir ]]; then
    echo "Output directory already exists: $output_dir" >&2
    exit 1
fi
case "$output_dir/" in
    "$source_dir/"*)
        echo 'Output must be outside the kernel source tree.' >&2
        exit 1
        ;;
esac

major=$(sed -n 's/^VERSION = //p' "$source_dir/Makefile")
minor=$(sed -n 's/^PATCHLEVEL = //p' "$source_dir/Makefile")
patch_file="$repo_dir/kernel/linux-$major.$minor.patch"
if [[ ! -f $patch_file ]]; then
    echo "Unsupported kernel source series: $major.$minor" >&2
    exit 1
fi

mkdir -p -- "$(dirname -- "$output_dir")"
staging_dir=$(mktemp -d -- "$output_dir.tmp.XXXXXX")
trap 'rm -rf -- "$staging_dir"' EXIT
cp -- "$source_dir/sound/usb/"*.c "$source_dir/sound/usb/"*.h "$staging_dir/"
cp -- "$source_dir/sound/usb/Makefile" "$staging_dir/Makefile"
patch --batch --fuzz=0 --directory="$staging_dir" -p3 < "$patch_file"

# Keep the ALSA implementation private so snd-usb-audio can coexist with it.
sed -E -i '/^EXPORT_SYMBOL(_GPL)?\([[:alnum:]_]+\);$/d' "$staging_dir/"*.c
# Use the kernel's existing MIDI helper; build only our dedicated audio module.
sed -i -e 's/snd-usb-audio/snd-octa-capture/g' \
    -e '/^obj-/d' -e '/^snd-usbmidi-lib-y[[:space:]]*:=/d' "$staging_dir/Makefile"
printf '\nobj-m += snd-octa-capture.o\n' >> "$staging_dir/Makefile"

mv -T -- "$staging_dir" "$output_dir"
trap - EXIT
printf 'Prepared standalone module sources in %s\n' "$output_dir"
