#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail

if (( $# < 1 )); then
    echo 'Usage: dkms-build.sh KERNEL_HEADERS [KBUILD_ARGUMENTS...]' >&2
    exit 2
fi
kernel_dir=$(realpath -e -- "$1")
shift
module_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
series=$(< "$module_dir/BUILD-SERIES")
if [[ ! -s $kernel_dir/Module.symvers || ! -s $kernel_dir/include/config/kernel.release ]]; then
    echo 'Matching, prepared kernel headers and Module.symvers are required.' >&2
    exit 1
fi
kernel_release=$(< "$kernel_dir/include/config/kernel.release")
if [[ $kernel_release != "$series."* ]]; then
    echo "This source package supports kernel series $series; target is $kernel_release." >&2
    exit 1
fi
compiler=${KERNEL_CC:-}
if [[ -z $compiler ]]; then
    for configuration in "$kernel_dir/.config" "$kernel_dir/include/config/auto.conf"; do
        [[ -r $configuration ]] || continue
        compiler=$(sed -n 's/^CONFIG_CC_VERSION_TEXT="*\([^" ]*\) .*/\1/p' "$configuration")
        [[ -z $compiler ]] || break
    done
fi
if [[ -z $compiler ]] || ! command -v "$compiler" >/dev/null; then
    echo 'Install the compiler used for this kernel, or set KERNEL_CC.' >&2
    exit 1
fi
jobs=${OCTA_BUILD_JOBS:-4}
if [[ ! $jobs =~ ^[1-9][0-9]?$ ]] || (( jobs > 64 )); then
    echo 'OCTA_BUILD_JOBS must be 1..64.' >&2
    exit 2
fi
make -C "$kernel_dir" M="$module_dir" "CC=$compiler" -j"$jobs" \
    W=1 KCFLAGS=-Werror "$@" modules
