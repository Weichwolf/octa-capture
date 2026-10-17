// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/Protocol.hpp"

#include <array>
#include <cstdint>
#include <expected>
#include <optional>
#include <vector>

namespace octa {

using InputMeters = std::array<std::uint16_t, 8>;
enum class MeterError : std::uint8_t { message, size, data };

[[nodiscard]] std::expected<InputMeters, MeterError> decode_input_meters(const Message &message);
// Matches the reference's 14-bit level conversion; zero uses its -96 dB sentinel.
[[nodiscard]] std::expected<double, MeterError> input_meter_db(std::uint16_t value);
[[nodiscard]] std::expected<std::vector<std::uint8_t>, ProtocolError>
encode_meter_switch(bool enabled);

// Bounded incremental receiver; ignores other valid device messages.
class MeterReceiver {
public:
  [[nodiscard]] std::expected<std::optional<InputMeters>, ProtocolError> feed(std::uint8_t byte);

private:
  SysExReceiver receiver_;
};

}
