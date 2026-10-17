// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/DspControl.hpp"
#include "octa/Meters.hpp"
#include "octa/Preamp.hpp"
#include "octa/Protocol.hpp"

#include <chrono>
#include <cstddef>
#include <expected>
#include <functional>
#include <string_view>

namespace octa {

struct MidiError {
  int code;
  std::string_view operation;
};

// Deadline covers write and reply.
[[nodiscard]] std::expected<Message, MidiError> read_bulk_state(std::string_view port);
// Validates before opening the port, writes DT1, then verifies a fresh bulk reply.
[[nodiscard]] std::expected<Message, MidiError> write_preamp(std::string_view port,
                                                             PreampChange change);

[[nodiscard]] std::expected<Message, MidiError> write_dsp(std::string_view port,
                                                          const DspChange &change);

// A listener returns false to end observation. Keep callbacks short/nonblocking.
using MeterListener = std::function<bool(const InputMeters &)>;
// One CTRL connection, bounded duration (1..60000 ms), three-second inactivity
// timeout. Attempts the disable command on every exit after enabling was tried.
[[nodiscard]] std::expected<std::size_t, MidiError> observe_input_meters(
    std::string_view port, std::chrono::milliseconds duration, const MeterListener &listener);

}
