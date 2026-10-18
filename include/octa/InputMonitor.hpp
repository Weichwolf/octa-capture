// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/Protocol.hpp"

#include <cstdint>
#include <expected>
#include <optional>
#include <vector>

namespace octa {

struct InputMonitorChange {
  // The original Windows sender accepts 0..11; nullopt addresses all inputs.
  // The live target below rejects channels 10/11, which have no mixer readback.
  std::optional<std::uint8_t> channel;
  bool enabled;
  std::uint16_t pan;
  std::uint32_t volume;
};

// Composite input mute/pan/volume command. This codec performs no device I/O.
[[nodiscard]] std::expected<std::vector<std::uint8_t>, ProtocolError>
encode_input_monitor(InputMonitorChange change);

// Predict the entire bulk reply, including linked mute/volume and broadcasts
// to all ten stored inputs even at 192 kHz. Individual inputs must be active.
[[nodiscard]] std::expected<Message, ProtocolError> input_monitor_target(const Message &before,
                                                                         InputMonitorChange change);

}
