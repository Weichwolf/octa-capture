// SPDX-License-Identifier: GPL-2.0-only
#include "octa/InputMonitor.hpp"

#include "octa/Protocol.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <limits>
#include <optional>
#include <string_view>

namespace {

bool rejects_invalid_values() {
  constexpr unsigned int byte_values = 256;
  constexpr std::uint8_t reference_channels = 12;
  for (unsigned int channel = 0; channel < byte_values; ++channel) {
    const auto result = octa::encode_input_monitor(
        {.channel = static_cast<std::uint8_t>(channel), .enabled = true, .pan = 0, .volume = 0});
    if (result.has_value() != (channel < reference_channels)) { return false; }
  }
  constexpr unsigned int pan_values = 65536;
  for (unsigned int pan = 0; pan < pan_values; ++pan) {
    const auto result = octa::encode_input_monitor({.channel = std::nullopt,
                                                    .enabled = false,
                                                    .pan = static_cast<std::uint16_t>(pan),
                                                    .volume = 0});
    if (result.has_value() != (pan <= 0x8000)) { return false; }
  }
  constexpr std::array invalid_volumes = {
      0x800000U, 0xffffffU, std::numeric_limits<std::uint32_t>::max()};
  return std::ranges::none_of(invalid_volumes, [](auto volume) {
    return octa::encode_input_monitor({.channel = 0, .enabled = true, .pan = 0, .volume = volume})
        .has_value();
  });
}

bool check_case(octa::InputMonitorChange change, bool emit) {
  constexpr std::uint8_t broadcast = 0x7f;
  constexpr std::uint8_t composite_parameter = 0x14;
  const auto frame = octa::encode_input_monitor(change);
  if (!frame) { return false; }
  const auto decoded = octa::decode(*frame);
  if (!decoded || decoded->payload.size() != 11 ||
      decoded->address !=
          octa::Address{0, 6, change.channel.value_or(broadcast), composite_parameter} ||
      decoded->payload.front() != static_cast<std::uint8_t>(!change.enabled)) {
    return false;
  }
  if (emit) {
    std::cout << std::dec << (change.channel ? static_cast<int>(*change.channel) : -1) << '\t'
              << change.enabled << '\t' << change.pan << '\t' << change.volume << '\t' << std::hex
              << std::setfill('0');
    for (const auto byte : *frame) { std::cout << std::setw(2) << static_cast<unsigned int>(byte); }
    std::cout << '\n';
  }
  return std::cout.good();
}

bool frames(bool emit) {
  constexpr std::array<std::uint16_t, 7> pans = {0, 1, 0x2345, 0x4000, 0x7ffe, 0x7fff, 0x8000};
  constexpr std::array<std::uint32_t, 5> volumes = {0, 1, 0x123456, 0x200000, 0x7fffff};
  constexpr unsigned int selectors = 13;
  for (unsigned int selector = 0; selector < selectors; ++selector) {
    const std::optional<std::uint8_t> channel =
        selector == 12 ? std::nullopt : std::optional(static_cast<std::uint8_t>(selector));
    for (const bool enabled : {false, true}) {
      for (const auto pan : pans) {
        for (const auto volume : volumes) {
          if (!check_case({.channel = channel, .enabled = enabled, .pan = pan, .volume = volume},
                          emit)) {
            return false;
          }
        }
      }
    }
  }
  return true;
}

}

int main(int argc, char **argv) {
  const bool emit = argc == 2 && std::string_view(argv[1]) == "--emit";
  if (argc != 1 && !emit) { return 2; }
  if (!rejects_invalid_values() || !frames(emit)) {
    std::cerr << "Input-monitor codec validation failed.\n";
    return 1;
  }
  return 0;
}
