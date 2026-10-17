// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Midi.hpp"
#include "octa/Preamp.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cstdint>
#include <iostream>
#include <iterator>
#include <optional>
#include <string_view>
#include <system_error>

namespace {

constexpr std::array<std::string_view, 13> names = {"phantom",
                                                    "low-cut",
                                                    "phase",
                                                    "hi-z",
                                                    "sensitivity",
                                                    "stereo-link",
                                                    "compressor",
                                                    "gate",
                                                    "attack",
                                                    "release",
                                                    "threshold",
                                                    "ratio",
                                                    "gain"};

std::optional<std::uint8_t> parse_byte(std::string_view text) {
  unsigned int value = 0;
  const auto result = std::from_chars(text.data(), text.data() + text.size(), value);
  constexpr unsigned int maximum_byte = 255;
  if (result.ec != std::errc{} || result.ptr != text.data() + text.size() || value > maximum_byte) {
    return std::nullopt;
  }
  return static_cast<std::uint8_t>(value);
}

int usage() {
  std::cerr << "Usage: octa-control CHANNEL(1..8) PARAMETER VALUE [hw:CTRL-port]\nParameters:";
  for (const auto name : names) { std::cerr << ' ' << name; }
  std::cerr << "\nValues are wire indices, not display units.\n";
  return 2;
}

}

int main(int argc, char **argv) {
  constexpr int required_arguments = 4;
  constexpr int maximum_arguments = 5;
  if (argc < required_arguments || argc > maximum_arguments) { return usage(); }
  const auto channel = parse_byte(argv[1]);
  const auto *const parameter = std::ranges::find(names, std::string_view(argv[2]));
  const auto value = parse_byte(argv[3]);
  if (!channel || *channel < 1 || *channel > 8 || parameter == names.end() || !value) {
    return usage();
  }
  const octa::PreampChange change = {
      .channel = static_cast<std::uint8_t>(*channel - 1),
      .parameter = static_cast<octa::PreampParameter>(std::distance(names.begin(), parameter)),
      .value = *value};
  const std::string_view port =
      argc == maximum_arguments ? argv[4] : "hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1";
  const auto result = octa::write_preamp(port, change);
  if (!result) {
    std::cerr << result.error().operation << ": ALSA error " << result.error().code << '\n';
    return 1;
  }
  std::cout << R"({"channel":)" << static_cast<unsigned int>(*channel) << R"(,"parameter":")"
            << *parameter << R"(","value":)" << static_cast<unsigned int>(*value)
            << R"(,"verified":true})" << '\n';
  return 0;
}
