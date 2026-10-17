// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Initialization.hpp"

#include "octa/Protocol.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <string_view>
#include <vector>

namespace {

bool require(bool passed, std::string_view description) {
  if (!passed) { std::cerr << "FAIL: " << description << '\n'; }
  return passed;
}

bool frames() {
  constexpr unsigned int byte_values = 256;
  constexpr std::array<std::uint8_t, 14> display_a = {
      0xf0, 0x41, 0x10, 0, 0, 0x4d, 0x12, 0, 2, 0, 3, 0, 0x7b, 0xf7};
  constexpr std::array<std::uint8_t, 14> initialize_all = {
      0xf0, 0x41, 0x10, 0, 0, 0x4d, 0x12, 0, 0, 0, 2, 0, 0x7e, 0xf7};
  constexpr std::array<std::uint8_t, 5> checksums = {0x7e, 0x7d, 0x7c, 0x7b, 0x7a};
  for (unsigned int index = 0; index < byte_values; ++index) {
    const auto frame = octa::encode_initialization(static_cast<octa::InitializationTarget>(index));
    if (index >= checksums.size()) {
      if (!require(!frame && frame.error() == octa::InitializationError::target,
                   "invalid selector rejected")) {
        return false;
      }
      continue;
    }
    auto command = initialize_all;
    command[11] = static_cast<std::uint8_t>(index);
    command[12] = checksums[index];
    std::vector<std::uint8_t> expected(command.begin(), command.end());
    if (index == 0 || index == 4) {
      expected.insert(expected.end(), display_a.begin(), display_a.end());
    }
    if (!require(frame && *frame == expected, "original initialization and display frames")) {
      return false;
    }
  }
  return true;
}

bool scopes() {
  const octa::Message before{.command = octa::Command::data,
                             .address = octa::bulk_address,
                             .payload = std::vector<std::uint8_t>(octa::bulk_size)};
  constexpr std::array<std::size_t, 5> examples = {6, 0x1d, 0x83, 0x0a, 6};
  for (std::size_t index = 0; index < examples.size(); ++index) {
    const auto target = static_cast<octa::InitializationTarget>(index);
    auto after = before;
    after.payload[examples[index]] = 1;
    const auto selected = octa::initialization_scope_matches(before, after, target);
    after.payload[2] = 1;
    const auto rate = octa::initialization_scope_matches(before, after, target);
    after.payload[2] = 0;
    after.payload[0] = 1;
    const auto system = octa::initialization_scope_matches(before, after, target);
    if (!require(selected && *selected && rate && !*rate && system && !*system,
                 "targeted state accepted; clock/system changes rejected")) {
      return false;
    }
  }
  auto after = before;
  after.payload[4] = 1;
  const auto display =
      octa::initialization_scope_matches(before, after, octa::InitializationTarget::patchbay);
  const auto unchanged_property =
      octa::initialization_scope_matches(after, after, octa::InitializationTarget::patchbay);
  after = before;
  constexpr std::size_t sensitivity = 0x1d;
  after.payload[sensitivity] = 1;
  const auto unrelated =
      octa::initialization_scope_matches(before, after, octa::InitializationTarget::reverb);
  after.payload.pop_back();
  const auto truncated =
      octa::initialization_scope_matches(before, after, octa::InitializationTarget::all);
  return require(
      display && !*display && unchanged_property && *unchanged_property && unrelated &&
          !*unrelated && !truncated && truncated.error() == octa::InitializationError::state,
      "property preserved or zero; unrelated selection/preamp and malformed state rejected");
}

}

int main() {
  return frames() && scopes() ? 0 : 1;
}
