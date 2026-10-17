// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/DspControl.hpp"
#include "octa/Preamp.hpp"
#include "octa/Protocol.hpp"

#include <expected>
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

}
