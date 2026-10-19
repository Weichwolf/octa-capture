// SPDX-License-Identifier: GPL-2.0-only
#include "octa-midi.h"

#include <array>
#include <iostream>

namespace {

using Packet = std::array<unsigned char, 4>;

struct InputCase {
  Packet packet;
  int length;
};

bool receive_filters() {
  // Fixed original Windows/macOS decoder observations, including opaque SysEx.
  constexpr std::array cases = {
      InputCase{.packet = {0x00, 0x90, 0x40, 0x7f}, .length = 0},
      InputCase{.packet = {0x11, 0x90, 0x40, 0x7f}, .length = 0},
      InputCase{.packet = {0x02, 0xf1, 0x40, 0xff}, .length = 2},
      InputCase{.packet = {0x12, 0xf3, 0x40, 0xff}, .length = 2},
      InputCase{.packet = {0x03, 0xf2, 0x40, 0x7f}, .length = 3},
      InputCase{.packet = {0x04, 0x90, 0x80, 0xff}, .length = 3},
      InputCase{.packet = {0x15, 0x90, 0xff, 0xff}, .length = 1},
      InputCase{.packet = {0x06, 0xf0, 0xf7, 0xff}, .length = 2},
      InputCase{.packet = {0x17, 0xf0, 0x7d, 0xf7}, .length = 3},
      InputCase{.packet = {0x08, 0x8f, 0x40, 0x7f}, .length = 3},
      InputCase{.packet = {0x19, 0x9f, 0x40, 0x7f}, .length = 3},
      InputCase{.packet = {0x0a, 0xaf, 0x40, 0x7f}, .length = 3},
      InputCase{.packet = {0x1b, 0xbf, 0x40, 0x7f}, .length = 3},
      InputCase{.packet = {0x0c, 0xcf, 0x40, 0xff}, .length = 2},
      InputCase{.packet = {0x1d, 0xdf, 0x40, 0xff}, .length = 2},
      InputCase{.packet = {0x0e, 0xef, 0x40, 0x7f}, .length = 3},
      InputCase{.packet = {0x1f, 0xf6, 0xff, 0xff}, .length = 1},
      InputCase{.packet = {0x0f, 0xf8, 0xff, 0xff}, .length = 1},
      InputCase{.packet = {0x1f, 0xff, 0xff, 0xff}, .length = 1},
      InputCase{.packet = {0x09, 0x80, 0x40, 0x7f}, .length = 0},
      InputCase{.packet = {0x09, 0x90, 0x80, 0x7f}, .length = 0},
      InputCase{.packet = {0x19, 0x90, 0x40, 0xff}, .length = 0},
      InputCase{.packet = {0x03, 0xf1, 0x40, 0x7f}, .length = 0},
      InputCase{.packet = {0x13, 0xf2, 0x40, 0xff}, .length = 0},
      InputCase{.packet = {0x0c, 0xc0, 0x80, 0}, .length = 0},
      InputCase{.packet = {0x02, 0xf2, 0x40, 0}, .length = 0},
      InputCase{.packet = {0x12, 0xf1, 0x80, 0}, .length = 0},
      InputCase{.packet = {0x0f, 0x90, 0, 0}, .length = 0},
  };
  for (const auto &item : cases) {
    if (octa_midi_event_length(item.packet.data()) != item.length) {
      std::cerr << "FAIL: reference input filter for USB header " << int{item.packet[0]} << '\n';
      return false;
    }
  }
  return true;
}

bool interrupted_sysex() {
  constexpr std::array<unsigned char, 2> pending_data = {0xf0, 0x7d};
  constexpr std::array expected = {
      Packet{0x05, 0xf7, 0, 0}, Packet{0x06, 0xf0, 0xf7, 0}, Packet{0x07, 0xf0, 0x7d, 0xf7}};
  constexpr std::array<unsigned char, 2> cables = {0x00, 0x10};
  for (const auto cable : cables) {
    for (unsigned int pending = 0; pending < expected.size(); ++pending) {
      Packet actual{};
      auto packet = expected[pending];
      packet[0] |= cable;
      if (!octa_midi_sysex_end(cable, pending, pending_data.data(), actual.data()) ||
          actual != packet) {
        std::cerr << "FAIL: reference SysEx termination packet\n";
        return false;
      }
    }
  }
  Packet unchanged = expected[0];
  if (octa_midi_sysex_end(0, 3, pending_data.data(), unchanged.data()) ||
      unchanged != expected[0]) {
    std::cerr << "FAIL: invalid pending count changed output\n";
    return false;
  }
  return true;
}

} // namespace

int main() {
  return receive_filters() && interrupted_sysex() ? 0 : 1;
}
