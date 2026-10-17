// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Midi.hpp"
#include "octa/Preamp.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <optional>
#include <span>
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
  std::cerr << "Usage: octa-control [--dry-run] CHANNEL(1..8) PARAMETER VALUE "
               "[hw:CTRL-port]\nParameters:";
  for (const auto name : names) { std::cerr << ' ' << name; }
  std::cerr << "\nValues are wire indices, not display units. Hi-Z uses channels 1/2; "
               "stereo-link uses 1/3/5/7. Dry run opens no device.\n";
  return 2;
}

int print_frame(std::span<const std::uint8_t> frame) {
  std::cout << R"({"frame_hex":")" << std::hex << std::setfill('0');
  for (const auto byte : frame) { std::cout << std::setw(2) << static_cast<unsigned int>(byte); }
  std::cout << "\"}\n" << std::flush;
  return std::cout.good() ? 0 : 1;
}

}

int main(int argc, char **argv) {
  auto arguments = std::span(argv, static_cast<std::size_t>(argc)).subspan(1);
  const bool dry_run = !arguments.empty() && std::string_view(arguments.front()) == "--dry-run";
  if (dry_run) { arguments = arguments.subspan(1); }
  constexpr std::size_t required_arguments = 3;
  constexpr std::size_t maximum_arguments = 4;
  if (arguments.size() < required_arguments || arguments.size() > maximum_arguments) {
    return usage();
  }
  const auto channel = parse_byte(arguments[0]);
  const auto *const parameter = std::ranges::find(names, std::string_view(arguments[1]));
  const auto value = parse_byte(arguments[2]);
  if (!channel || *channel < 1 || *channel > 8 || parameter == names.end() || !value) {
    return usage();
  }
  const octa::PreampChange change = {
      .channel = static_cast<std::uint8_t>(*channel - 1),
      .parameter = static_cast<octa::PreampParameter>(std::distance(names.begin(), parameter)),
      .value = *value};
  const auto frame = octa::encode_preamp_change(change);
  if (!frame) {
    std::cerr << "Invalid preamp channel, parameter or value.\n";
    return 2;
  }
  if (dry_run) { return print_frame(*frame); }
  const std::string_view port =
      arguments.size() == maximum_arguments ? arguments[3] : "hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1";
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
