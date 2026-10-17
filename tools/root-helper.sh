#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail
shopt -s nullglob
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

if (( EUID != 0 )) || [[ $# != 1 ]]; then
    echo 'Use sudo octa-capture-dev {status|install|unload|detach}.' >&2
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
    detach)
        for device in /sys/bus/usb/devices/*; do
            [[ -r $device/idVendor && -r $device/idProduct ]] || continue
            [[ $(< "$device/idVendor") == 0582 && $(< "$device/idProduct") == 0120 ]] || continue
            busid=${device##*/}
            for controller in /sys/devices/platform/vhci_hcd.*; do
                [[ -r $controller/status ]] || continue
                while read -r _ port _ _ _ _ local_busid; do
                    if [[ $local_busid == "$busid" ]]; then
                        printf '%s' "$((10#$port))" > "$controller/detach"
                        echo "Detached OCTA-CAPTURE from USB/IP port $port."
                        exit 0
                    fi
                done < "$controller/status"
            done
        done
        echo 'No attached OCTA-CAPTURE USB/IP port found.' >&2
        exit 1
        ;;
    *)
        echo 'Allowed actions: status, install, unload, detach.' >&2
        exit 2
        ;;
esac
