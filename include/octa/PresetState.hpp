// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/Preset.hpp"
#include "octa/Protocol.hpp"

#include <cstdint>
#include <expected>

namespace octa {

enum class PresetStateError : std::uint8_t { message, data, range };

// Converts a validated bulk reply to the reference's disk preset layout.
// Fields absent from the disk format (e.g. mono and clock) cannot be exported.
// Only mixer A input send/return slots are populated by the reference bulk path.
// The unused B-D input returns retain the reference constructor default (unity).
[[nodiscard]] std::expected<Preset, PresetStateError> preset_from_bulk(const Message &message);

// Prepares the complete reference-style DT1 bulk payload without device I/O.
// Validates all effective values against existing typed control bounds. Inactive
// file slots are ignored as in the reference. Mono/unmapped mixer flags reset;
// cached device properties in the current bulk prefix are retained/canonicalized.
[[nodiscard]] std::expected<Message, PresetStateError> preset_to_bulk(const Preset &preset,
                                                                      const Message &current);

}
