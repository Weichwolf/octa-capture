// SPDX-License-Identifier: GPL-2.0-only
#include "octa/PanelCommand.hpp"

#include "octa/Protocol.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <vector>

namespace {

bool frames() {
  constexpr std::array<std::uint8_t, 14> identify = {
      0xf0, 0x41, 0x10, 0, 0, 0x4d, 0x12, 0, 0, 0, 0, 0, 0, 0xf7};
  const auto blink = octa::encode_panel_command({.action = octa::PanelAction::identify});
  if (!blink || !std::ranges::equal(*blink, identify)) { return false; }
  constexpr unsigned int byte_values = 256;
  constexpr std::uint8_t display_checksum = 0x7b;
  for (unsigned int bus = 0; bus < byte_values; ++bus) {
    const auto selected = static_cast<std::uint8_t>(bus);
    const auto frame =
        octa::encode_panel_command({.action = octa::PanelAction::select_mixer, .bus = selected});
    if (frame.has_value() != (bus < 4)) { return false; }
    if (frame) {
      const std::array<std::uint8_t, 14> expected = {
          0xf0,
          0x41,
          0x10,
          0,
          0,
          0x4d,
          0x12,
          0,
          2,
          0,
          3,
          selected,
          static_cast<std::uint8_t>(display_checksum - selected),
          0xf7};
      if (!std::ranges::equal(*frame, expected)) { return false; }
    }
    const auto action = static_cast<octa::PanelAction>(bus);
    if (octa::encode_panel_command({.action = action}).has_value() != (bus <= 1)) { return false; }
    if (octa::encode_panel_command({.action = octa::PanelAction::identify, .bus = selected})
            .has_value() != (bus == 0)) {
      return false;
    }
  }
  return true;
}

bool preservation() {
  octa::Message before = {.command = octa::Command::data,
                          .address = octa::bulk_address,
                          .payload = std::vector<std::uint8_t>(octa::bulk_size)};
  before.payload[4] = 1;
  constexpr octa::PanelCommand command = {.action = octa::PanelAction::select_mixer, .bus = 3};
  if (!octa::panel_command_preserves_state(before, before, command)) { return false; }
  auto after = before;
  after.payload[4] = 3;
  if (!octa::panel_command_preserves_state(before, after, command)) { return false; }
  after.payload[4] = 2;
  if (octa::panel_command_preserves_state(before, after, command)) { return false; }
  for (std::size_t index = 0; index < before.payload.size(); ++index) {
    after = before;
    after.payload[index] ^= 1;
    if (octa::panel_command_preserves_state(before, after, command) ||
        octa::panel_command_preserves_state(
            before, after, {.action = octa::PanelAction::identify})) {
      return false;
    }
  }
  after = before;
  after.payload.pop_back();
  return !octa::panel_command_preserves_state(before, after, command);
}

}

int main() {
  if (!frames() || !preservation()) {
    std::cerr << "Panel command frame/bounds/preservation regression.\n";
    return 1;
  }
  return 0;
}
