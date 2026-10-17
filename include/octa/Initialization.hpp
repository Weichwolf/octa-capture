// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/Protocol.hpp"

#include <cstdint>
#include <expected>
#include <vector>

namespace octa {

enum class InitializationTarget : std::uint8_t { all, preamps, direct_mix, reverb, patchbay };
enum class InitializationError : std::uint8_t { target, state };

// Original Initialize menu sequence, including Mixer A display selection for
// All/Patch Bay. Firmware supplies defaults.
[[nodiscard]] std::expected<std::vector<std::uint8_t>, InitializationError>
encode_initialization(InitializationTarget target);

// Verify state bounds and preservation outside the selected settings group.
// This does not prove that firmware has finished initializing the group.
[[nodiscard]] std::expected<bool, InitializationError> initialization_scope_matches(
    const Message &before, const Message &after, InitializationTarget target);

}
