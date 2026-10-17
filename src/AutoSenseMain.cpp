// SPDX-License-Identifier: GPL-2.0-only
#include "PresetDevice.hpp"
#include "octa/AutoSense.hpp"

#include <charconv>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <optional>
#include <string_view>
#include <system_error>

namespace {

std::optional<unsigned int> parse_number(std::string_view text, unsigned int maximum) {
  unsigned int value = 0;
  const auto result = std::from_chars(text.data(), text.data() + text.size(), value);
  if (result.ec != std::errc{} || result.ptr != text.data() + text.size() || value == 0 ||
      value > maximum) {
    return std::nullopt;
  }
  return value;
}

int usage() {
  std::cerr << "Usage: octa-auto-sense MASK(1..255) DURATION_MS(1..60000) finish|cancel "
               "NEW-RECOVERY.syx [hw:CTRL-port]\n"
               "MASK is decimal; bit 0 selects input 1. Cancel restores the complete raw state.\n";
  return 2;
}

}

int main(int argc, char **argv) {
  constexpr int required_arguments = 5;
  constexpr int maximum_arguments = 6;
  if (argc < required_arguments || argc > maximum_arguments) { return usage(); }
  constexpr unsigned int maximum_mask = 255;
  constexpr unsigned int maximum_duration_ms = 60000;
  const auto mask = parse_number(argv[1], maximum_mask);
  const auto duration = parse_number(argv[2], maximum_duration_ms);
  const std::string_view action = argv[3];
  if (!mask || !duration || (action != "finish" && action != "cancel")) { return usage(); }
  const octa::cli::AutoSenseOptions request = {
      .channels = static_cast<std::uint8_t>(*mask),
      .duration = std::chrono::milliseconds(static_cast<std::int64_t>(*duration)),
      .action = action == "finish" ? octa::AutoSenseAction::finish : octa::AutoSenseAction::cancel};
  const std::string_view port =
      argc == maximum_arguments ? argv[5] : "hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1";
  return octa::cli::run_auto_sense(request, {.port = port, .snapshot_path = argv[4]});
}
