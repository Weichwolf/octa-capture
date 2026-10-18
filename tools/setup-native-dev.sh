#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Install native Debian development dependencies and device-specific access.
set -euo pipefail
shopt -s nullglob
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

if (( EUID != 0 )) || (( $# > 1 )); then
    echo "Usage: sudo $0 [USER]" >&2
    exit 2
fi
target_user=${1:-${SUDO_USER:-}}
if [[ ! $target_user =~ ^[a-z_][a-z0-9_-]*$ ]] ||
   ! id "$target_user" > /dev/null 2>&1 || [[ $(id -u "$target_user") == 0 ]]; then
    echo 'Run through sudo as the development user, or name that user explicitly.' >&2
    exit 1
fi
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
kernel_release=$(uname -r)
case "$kernel_release" in
    6.12.*) ;;
    *) echo "This setup targets native Debian kernel 6.12, found $kernel_release." >&2; exit 1 ;;
esac
module_file="$repo_dir/build/driver-native/snd-octa-capture.ko"
if [[ ! -s $module_file ]]; then
    echo "Build the native module first; expected $module_file." >&2
    exit 1
fi
module_release=$(modinfo -F vermagic "$module_file")
if [[ $(modinfo -F name "$module_file") != snd_octa_capture ||
      ${module_release%% *} != "$kernel_release" ]]; then
    echo 'The native module must match the running kernel.' >&2
    exit 1
fi

apt-get update
apt-get install -y --no-install-recommends \
    build-essential gcc-14 clang-19 clang-tidy-19 clang-format-19 libclang-rt-19-dev lld-19 \
    cmake ninja-build pkg-config libasound2-dev libusb-1.0-0-dev libelf-dev dwarves \
    "linux-headers-$kernel_release" linux-source-6.12 dkms sparse \
    alsa-utils usbutils kmod acl sudo psmisc git gh gdb binutils elfutils \
    openjdk-21-jdk-headless unzip curl file \
    python3 python3-gi python3-usb1 python3-unicorn \
    gir1.2-gtk-4.0 gir1.2-webkit-6.0 gir1.2-atspi-2.0 xvfb xauth dbus-x11 xdotool imagemagick \
    chromium nodejs npm node-undici eslint shellcheck

usermod -a -G audio "$target_user"
install -d -m 0755 /etc/udev/rules.d
cat > /etc/udev/rules.d/70-octa-capture-dev.rules <<'RULES'
# OCTA-CAPTURE development access; other USB devices are unchanged.
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="0582", ATTR{idProduct}=="0120", GROUP="audio", MODE="0660", TAG+="uaccess"
SUBSYSTEM=="sound", ATTRS{idVendor}=="0582", ATTRS{idProduct}=="0120", GROUP="audio", MODE="0660", TAG+="uaccess"
RULES
udevadm control --reload-rules
"$repo_dir/tools/grant-dev-access.sh" "$target_user" "$module_file"
printf 'KDIR=%q\nKERNEL_CC=gcc-14\nKDIR_NATIVE=%q\n' \
    "/lib/modules/$kernel_release/build" "/lib/modules/$kernel_release/build" \
    > /etc/octa-capture-dev.env
chmod 0644 /etc/octa-capture-dev.env

# Observe only the bus containing OCTA-CAPTURE. ACLs last until reboot; rerun
# this script if bus numbering or the boot session changes.
modprobe usbmon
udevadm settle --timeout=10
found=false
for device in /sys/bus/usb/devices/*; do
    [[ -r $device/idVendor && -r $device/idProduct ]] || continue
    [[ $(< "$device/idVendor") == 0582 && $(< "$device/idProduct") == 0120 ]] || continue
    found=true
    bus_number=$(< "$device/busnum")
    device_number=$(< "$device/devnum")
    printf -v usb_node '/dev/bus/usb/%03d/%03d' "$((10#$bus_number))" "$((10#$device_number))"
    setfacl -m "u:$target_user:rw" "$usb_node" "/dev/usbmon$((10#$bus_number))"
done
if ! $found; then
    echo 'No OCTA-CAPTURE connected; bus ACLs will need setup after connection.' >&2
fi

echo 'Native tools and OCTA-CAPTURE development access are ready.'
echo 'The agent can now install the prepared native module through its development helper.'
