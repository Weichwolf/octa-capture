// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include <cstdint>
#include <expected>

namespace octa {

struct ClockStatus {
  std::uint32_t sample_rate_hz;
  std::uint8_t flags;
  std::uint16_t product_id;
  bool operator==(const ClockStatus &) const = default;
};

struct UsbError {
  int code;
};

// Uses the reference driver's vendor IN request; does not claim interfaces or write settings.
[[nodiscard]] std::expected<ClockStatus, UsbError> read_clock_status();

}
