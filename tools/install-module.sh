#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail
shopt -s nullglob

if [[ $# != 1 ]]; then
    echo "Usage: sudo $0 /path/to/snd-octa-capture.ko" >&2
    exit 2
fi
if (( EUID != 0 )); then
    echo 'Installing and binding a kernel module requires root.' >&2
    exit 1
fi

module_file=$(realpath -e -- "$1")
kernel_release=$(uname -r)
module_release=$(modinfo -F vermagic "$module_file")
if [[ $(modinfo -F name "$module_file") != snd_octa_capture ||
      ${module_release%% *} != "$kernel_release" ]]; then
    echo 'Expected snd-octa-capture built for the running kernel.' >&2
    exit 1
fi

if [[ -d /sys/module/snd_octa_capture ]]; then
    modprobe -r snd-octa-capture
fi
install -D -m 0644 -- "$module_file" \
    "/lib/modules/$kernel_release/updates/octa-capture/snd-octa-capture.ko"
install -d -m 0755 /etc/modprobe.d
printf '%s\n' '# Prefer the dedicated OCTA-CAPTURE driver before generic USB audio.' \
    'softdep snd-usb-audio pre: snd-octa-capture' > /etc/modprobe.d/octa-capture.conf
depmod -a "$kernel_release"
modprobe snd-octa-capture

for device in /sys/bus/usb/devices/*; do
    [[ -r $device/idVendor && -r $device/idProduct ]] || continue
    [[ $(< "$device/idVendor") == 0582 && $(< "$device/idProduct") == 0120 ]] || continue
    interfaces=("$device":*)
    (( ${#interfaces[@]} > 0 )) || continue
    for interface in "${interfaces[@]}"; do
        [[ -L $interface/driver ]] || continue
        driver=$(basename -- "$(readlink -f -- "$interface/driver")")
        if [[ $driver != snd-usb-audio && $driver != snd-octa-capture ]]; then
            echo "OCTA-CAPTURE interface is owned by unexpected driver: $driver" >&2
            exit 1
        fi
    done
    for interface in "${interfaces[@]}"; do
        [[ -L $interface/driver ]] || continue
        driver=$(basename -- "$(readlink -f -- "$interface/driver")")
        if [[ $driver == snd-usb-audio ]]; then
            printf '%s' "${interface##*/}" > "$interface/driver/unbind"
        fi
    done
    if [[ ! -L ${interfaces[0]}/driver ]]; then
        printf '%s' "${interfaces[0]##*/}" > /sys/bus/usb/drivers/snd-octa-capture/bind
    fi
done
echo 'Installed snd-octa-capture for the running kernel.'
