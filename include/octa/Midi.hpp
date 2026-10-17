// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/Protocol.hpp"

#include <expected>
#include <string_view>

namespace octa {

struct MidiError {
  int code;
  std::string_view operation;
};

// Sends only the reference bulk-state request; deadline covers write and reply.
[[nodiscard]] std::expected<Message, MidiError> read_bulk_state(std::string_view port);

}
