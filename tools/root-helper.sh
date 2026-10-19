#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail
shopt -s nullglob
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

if (( EUID != 0 )) || [[ $# != 1 ]]; then
    echo 'Use sudo octa-capture-dev {status|install|unload|runtime-pm}.' >&2
    exit 2
fi

case "$1" in
    status)
        ps -C usbreset,modprobe -o pid,stat,wchan:32,comm || true
        while read -r pid; do
            [[ -r /proc/$pid/stack ]] || continue
            printf '\nPID %s kernel stack:\n' "$pid"
            cat -- "/proc/$pid/stack"
        done < <(pgrep -x 'usbreset|modprobe' || true)
        ;;
    install)
        module_file=$(< /etc/octa-capture-dev-module)
        exec /usr/local/libexec/octa-capture/install-module "$module_file"
        ;;
    unload)
        exec modprobe -r snd-octa-capture
        ;;
    runtime-pm)
        exec /usr/local/libexec/octa-capture/runtime-pm
        ;;
    *)
        echo 'Allowed actions: status, install, unload, runtime-pm.' >&2
        exit 2
        ;;
esac
