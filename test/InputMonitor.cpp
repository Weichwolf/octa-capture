// SPDX-License-Identifier: GPL-2.0-only
#include "octa/InputMonitor.hpp"

#include "octa/Dsp.hpp"
#include "octa/Protocol.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <limits>
#include <optional>
#include <string_view>
#include <vector>

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

bool unchanged_outside_inputs(const octa::Message &before, const octa::Message &after) {
  constexpr std::size_t mute_offset = 0x74;
  constexpr std::size_t mute_end = 0x77;
  constexpr std::size_t pan_offset = 0x80;
  constexpr std::size_t pan_end = 0xa8;
  constexpr std::size_t volume_offset = 0x120;
  constexpr std::size_t volume_end = 0x15c;
  constexpr std::uint8_t reserved_mute_bits = 0x70;
  constexpr std::uint8_t last_mute_reserved = 0x7c;
  for (std::size_t index = 0; index < before.payload.size(); ++index) {
    if ((index >= pan_offset && index < pan_end) ||
        (index >= volume_offset && index < volume_end)) {
      continue;
    }
    auto old_value = before.payload[index];
    auto new_value = after.payload[index];
    if (index >= mute_offset && index < mute_end) {
      const auto mask = index == mute_end - 1 ? last_mute_reserved : reserved_mute_bits;
      old_value &= mask;
      new_value &= mask;
    }
    if (old_value != new_value) { return false; }
  }
  return before.command == after.command && before.address == after.address;
}

struct TargetCase {
  std::uint8_t rate;
  unsigned int selector;
  bool linked;
  bool enabled;
};

bool target_case(TargetCase sample) {
  const auto [rate, selector, linked, enabled] = sample;
  octa::Message before = {.command = octa::Command::data,
                          .address = octa::bulk_address,
                          .payload = std::vector<std::uint8_t>(octa::bulk_size)};
  constexpr std::size_t reserved_master_flags = 0x442;
  constexpr std::uint8_t unknown_bits = 0x55;
  constexpr std::uint8_t all_flags = 0x7f;
  constexpr std::uint8_t reserved_flags = 0x70;
  before.payload[1] = all_flags;
  before.payload[2] = rate;
  before.payload[reserved_master_flags] = unknown_bits;
  constexpr std::array<std::size_t, 2> links = {0x58, 0x59};
  constexpr std::array<std::size_t, 3> mutes = {0x74, 0x75, 0x76};
  for (const auto offset : links) { before.payload[offset] = linked ? all_flags : reserved_flags; }
  for (const auto offset : mutes) { before.payload[offset] = enabled ? all_flags : reserved_flags; }
  constexpr std::size_t mono_offset = 0x60;
  before.payload[mono_offset] = unknown_bits;
  constexpr std::uint16_t pan = 0x2345;
  constexpr std::uint32_t volume = 0x123456;
  const std::optional<std::uint8_t> channel =
      selector == 12 ? std::nullopt : std::optional(static_cast<std::uint8_t>(selector));
  const auto target = octa::input_monitor_target(
      before, {.channel = channel, .enabled = enabled, .pan = pan, .volume = volume});
  const unsigned int active = rate == 3 ? 4 : 10;
  if (target.has_value() != (!channel || *channel < active)) { return false; }
  if (!target) { return true; }
  if (!unchanged_outside_inputs(before, *target)) { return false; }
  const auto decoded = octa::decode_dsp(*target);
  if (!decoded) { return false; }
  for (std::size_t input = 0; input < 10; ++input) {
    const bool addressed = !channel || input == *channel;
    const bool peer = channel && linked && input == (*channel ^ 1U);
    const auto &actual = decoded->mixers[0].input.channels[input];
    if (actual.pan != (addressed ? pan : 0) ||
        actual.volume != ((addressed || peer) ? volume : 0) ||
        actual.mute != ((addressed || peer) ? !enabled : enabled)) {
      return false;
    }
  }
  return true;
}

bool targets() {
  for (std::uint8_t rate = 0; rate < 4; ++rate) {
    for (const bool linked : {false, true}) {
      for (const bool enabled : {false, true}) {
        for (unsigned int selector = 0; selector < 13; ++selector) {
          if (!target_case(
                  {.rate = rate, .selector = selector, .linked = linked, .enabled = enabled})) {
            return false;
          }
        }
      }
    }
  }
  octa::Message invalid = {
      .command = octa::Command::data, .address = octa::bulk_address, .payload = {}};
  const octa::InputMonitorChange change = {.channel = 0, .enabled = true, .pan = 0, .volume = 0};
  if (octa::input_monitor_target(invalid, change)) { return false; }
  invalid.payload.resize(octa::bulk_size);
  invalid.payload[2] = 4;
  if (octa::input_monitor_target(invalid, change)) { return false; }
  invalid.payload[2] = 0;
  constexpr std::size_t pan_offset = 0x80;
  invalid.payload[pan_offset] = 16;
  return !octa::input_monitor_target(invalid, change);
}

}

int main(int argc, char **argv) {
  const bool emit = argc == 2 && std::string_view(argv[1]) == "--emit";
  if (argc != 1 && !emit) { return 2; }
  if (!rejects_invalid_values() || !targets() || !frames(emit)) {
    std::cerr << "Input-monitor codec validation failed.\n";
    return 1;
  }
  return 0;
}
