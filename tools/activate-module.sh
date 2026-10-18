#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Bind only 0582:0120 to an already installed standalone module.
set -euo pipefail
shopt -s nullglob

check=false
if [[ $# == 1 && $1 == --check ]]; then
    check=true
elif (( $# != 0 )); then
    echo 'Usage: octa-capture-activate [--check]' >&2
    exit 2
fi
if ! $check; then
    if (( EUID != 0 )); then
        echo 'Device activation requires root; --check is read-only.' >&2
        exit 1
    fi
    modprobe snd-octa-capture
fi

found=false
ready=true
for device in /sys/bus/usb/devices/*; do
    [[ -r $device/idVendor && -r $device/idProduct ]] || continue
    [[ $(< "$device/idVendor") == 0582 && $(< "$device/idProduct") == 0120 ]] || continue
    found=true
    interfaces=("$device":*)
    if (( ${#interfaces[@]} == 0 )); then
        echo "No interfaces present: ${device##*/}" >&2
        exit 1
    fi
    needs_binding=false
    has_dedicated=false
    has_generic=false
    for interface in "${interfaces[@]}"; do
        [[ -L $interface/driver ]] || continue
        driver=$(basename -- "$(readlink -f -- "$interface/driver")")
        if [[ $driver != snd-usb-audio && $driver != snd-octa-capture ]]; then
            echo "Unexpected OCTA-CAPTURE interface owner: $driver" >&2
            exit 1
        fi
        if [[ $driver == snd-usb-audio ]]; then
            needs_binding=true
            has_generic=true
        else
            has_dedicated=true
        fi
    done
    if $has_dedicated && $has_generic; then
        echo "Mixed OCTA-CAPTURE interface owners: ${device##*/}" >&2
        exit 1
    fi
    if [[ ! -L ${interfaces[0]}/driver ]]; then
        needs_binding=true
    fi
    if $needs_binding; then
        if $check; then
            echo "OCTA-CAPTURE ${device##*/}: activation needed."
            ready=false
            continue
        fi
        for interface in "${interfaces[@]}"; do
            [[ -L $interface/driver ]] || continue
            driver=$(basename -- "$(readlink -f -- "$interface/driver")")
            if [[ $driver == snd-usb-audio ]]; then
                printf '%s' "${interface##*/}" > "$interface/driver/unbind"
            fi
        done
        if ! printf '%s' "${interfaces[0]##*/}" > /sys/bus/usb/drivers/snd-octa-capture/bind; then
            echo "Could not bind OCTA-CAPTURE ${device##*/}." >&2
            if $has_generic; then
                if modprobe snd-usb-audio &&
                   printf '%s' "${interfaces[0]##*/}" > /sys/bus/usb/drivers/snd-usb-audio/bind; then
                    echo 'Restored the previous generic driver binding.' >&2
                else
                    echo 'Could not restore the previous generic driver binding.' >&2
                fi
            fi
            exit 1
        fi
    fi
    echo "OCTA-CAPTURE ${device##*/}: snd-octa-capture active."
done
if ! $found; then
    echo 'No OCTA-CAPTURE USB device found.' >&2
    exit 1
fi
$ready
