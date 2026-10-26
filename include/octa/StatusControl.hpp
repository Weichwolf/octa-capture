// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/DeviceStatus.hpp"

#include <alsa/asoundlib.h>
#include <expected>
#include <string_view>

namespace octa {

struct StatusReadError {
  int code;
  std::string_view operation;
};

// Hardware control ports only, e.g. hw:CARD=OCTACAPTURE. Opens read-only;
// validates control metadata before reading its fixed eight-byte value.
[[nodiscard]] std::expected<DeviceStatus, StatusReadError>
read_device_status(std::string_view control_port);

// Reads an already-open read-only hardware control without reopening its card
// number. The caller retains ownership; disconnected handles fail normally.
[[nodiscard]] std::expected<DeviceStatus, StatusReadError>
read_device_status_handle(snd_ctl_t *control);

}
