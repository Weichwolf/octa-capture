#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail

if (( EUID != 0 )) || [[ $# != 2 ]]; then
    echo "Usage: sudo $0 USER /path/to/snd-octa-capture.ko" >&2
    exit 2
fi
target_user=$1
if [[ ! $target_user =~ ^[a-z_][a-z0-9_-]*$ ]] || ! id "$target_user" > /dev/null 2>&1; then
    echo 'Expected an existing local user name.' >&2
    exit 1
fi
module_file=$(realpath -e -- "$2")
if [[ $(modinfo -F name "$module_file") != snd_octa_capture ]]; then
    echo 'Expected the OCTA-CAPTURE module.' >&2
    exit 1
fi
tools_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

rules_file=$(mktemp)
trap 'rm -f -- "$rules_file"' EXIT
printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/octa-capture-dev status, /usr/local/sbin/octa-capture-dev install, /usr/local/sbin/octa-capture-dev unload, /usr/local/sbin/octa-capture-dev detach\n' \
    "$target_user" > "$rules_file"
visudo -cf "$rules_file"

install -d -m 0755 /usr/local/libexec/octa-capture /usr/local/sbin
install -o root -g root -m 0755 "$tools_dir/root-helper.sh" /usr/local/sbin/octa-capture-dev
install -o root -g root -m 0755 "$tools_dir/install-module.sh" /usr/local/libexec/octa-capture/install-module
printf '%s\n' "$module_file" > /etc/octa-capture-dev-module
chown root:root /etc/octa-capture-dev-module
chmod 0644 /etc/octa-capture-dev-module
install -o root -g root -m 0440 "$rules_file" /etc/sudoers.d/octa-capture-dev
echo "Granted OCTA-CAPTURE development commands to $target_user."
