// SPDX-License-Identifier: GPL-2.0-only
#include "octa/PanelCommand.hpp"

#include "octa/PresetState.hpp"
#include "octa/Protocol.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <vector>

namespace octa {

std::expected<std::vector<std::uint8_t>, ProtocolError> encode_panel_command(PanelCommand command) {
  Address address{};
  switch (command.action) {
    case PanelAction::select_mixer:
      if (command.bus >= 4) { return std::unexpected(ProtocolError::midi_data); }
      address = {0, 2, 0, 3};
      break;
    case PanelAction::identify:
      if (command.bus != 0) { return std::unexpected(ProtocolError::midi_data); }
      break;
    default: return std::unexpected(ProtocolError::midi_data);
  }
  const std::array payload = {command.bus};
  return encode(Command::data, address, payload);
}

bool panel_command_preserves_state(const Message &before,
                                   const Message &after,
                                   PanelCommand command) {
  if (!encode_panel_command(command) || !validate_bulk_write(before) ||
      !validate_bulk_write(after)) {
    return false;
  }
  constexpr std::size_t display_offset = 4;
  for (std::size_t index = 0; index < bulk_size; ++index) {
    if (index == display_offset && command.action == PanelAction::select_mixer &&
        after.payload[index] == command.bus) {
      continue;
    }
    if (before.payload[index] != after.payload[index]) { return false; }
  }
  return true;
}

}
