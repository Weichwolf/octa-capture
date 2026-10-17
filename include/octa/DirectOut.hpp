// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/DspControl.hpp"
#include "octa/Protocol.hpp"

#include <cstddef>
#include <cstdint>
#include <expected>
#include <vector>

namespace octa {

inline constexpr std::size_t direct_out_change_count = 206;
enum class DirectOutError : std::uint8_t { message, range, rate };

// Original Directly Output the Inputs sequence. No device access.
[[nodiscard]] std::vector<DspChange> direct_out_changes();
// Complete target with preamps/playback/coaxial route/opaque fields retained.
// Direct Out needs all four mixers; reject 192 kHz and unknown rate indices.
[[nodiscard]] std::expected<Message, DirectOutError> direct_out_target(const Message &current);

}
