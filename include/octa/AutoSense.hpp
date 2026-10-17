// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/Protocol.hpp"

#include <array>
#include <cstdint>
#include <expected>
#include <vector>

namespace octa {

using AutoSenseChannels = std::array<bool, 8>;
enum class AutoSenseAction : std::uint8_t { finish, cancel };
enum class AutoSenseError : std::uint8_t { message, size, data };

// Channel 1 is mask bit 0. Start requires at least one selected input.
[[nodiscard]] std::expected<std::vector<std::uint8_t>, AutoSenseError>
encode_auto_sense_start(std::uint8_t channels);
[[nodiscard]] std::expected<std::vector<std::uint8_t>, AutoSenseError>
encode_auto_sense_stop(AutoSenseAction action);
// Individual notifications only: bulk replies do not contain this activity bank.
// Malformed/unknown messages leave the complete bank unchanged.
[[nodiscard]] std::expected<bool, AutoSenseError>
apply_auto_sense_update(AutoSenseChannels &channels, const Message &message);

}
