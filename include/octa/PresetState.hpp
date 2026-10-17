// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/Preset.hpp"
#include "octa/Protocol.hpp"

#include <cstdint>
#include <expected>

namespace octa {

enum class PresetStateError : std::uint8_t { message, data };

// Converts a validated bulk reply to the reference's disk preset layout.
// Fields absent from the disk format (e.g. mono and clock) cannot be exported.
// Only mixer A input send/return slots are populated by the reference bulk path.
// The unused B-D input returns retain the reference constructor default (unity).
[[nodiscard]] std::expected<Preset, PresetStateError> preset_from_bulk(const Message &message);

}
