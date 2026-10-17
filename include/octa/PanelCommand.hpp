// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/Protocol.hpp"

#include <cstdint>
#include <expected>
#include <vector>

namespace octa {

enum class PanelAction : std::uint8_t { select_mixer, identify };

struct PanelCommand {
  PanelAction action;
  std::uint8_t bus = 0;
  bool operator==(const PanelCommand &) const = default;
};

// Display/identification commands, separate from audio DSP parameters.
[[nodiscard]] std::expected<std::vector<std::uint8_t>, ProtocolError>
encode_panel_command(PanelCommand command);

// A bulk reply proves audio-state preservation; it does not acknowledge the LCD.
[[nodiscard]] bool
panel_command_preserves_state(const Message &before, const Message &after, PanelCommand command);

}
