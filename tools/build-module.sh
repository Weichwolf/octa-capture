#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail

if [[ $# != 2 ]]; then
    echo "Usage: KDIR=... KERNEL_CC=... $0 KERNEL_SOURCE OUTPUT_DIRECTORY" >&2
    exit 2
fi

source_dir=$(realpath -e -- "$1")
kernel_dir=$(realpath -e -- "${KDIR:-/lib/modules/$(uname -r)/build}")
output_dir=$(realpath -m -- "$2")
tools_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

if [[ ! -s $kernel_dir/Module.symvers || ! -s $kernel_dir/include/config/kernel.release ]]; then
    echo 'Matching, fully prepared kernel build files and Module.symvers are required.' >&2
    exit 1
fi

makefile_value() {
    sed -n "s/^$1 = //p" "$source_dir/Makefile"
}
source_version="$(makefile_value VERSION).$(makefile_value PATCHLEVEL).$(makefile_value SUBLEVEL)$(makefile_value EXTRAVERSION)"
kernel_release=$(< "$kernel_dir/include/config/kernel.release")
if [[ ${kernel_release%%[-+]*} != "$source_version" ]]; then
    echo "Source version $source_version does not match kernel $kernel_release." >&2
    exit 1
fi

compiler=${KERNEL_CC:-$(sed -n 's/^CONFIG_CC_VERSION_TEXT="\([^ ]*\).*/\1/p' "$kernel_dir/.config")}
if [[ -z $compiler ]] || ! command -v "$compiler" > /dev/null; then
    echo 'Set KERNEL_CC to the compiler used for the target kernel.' >&2
    exit 1
fi

"$tools_dir/prepare-alsa.sh" "$source_dir" "$output_dir"
make -C "$kernel_dir" M="$output_dir" "CC=$compiler" \
    W=1 KCFLAGS=-Werror modules
