// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Initialization.hpp"

#include "octa/PresetState.hpp"
#include "octa/Protocol.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <vector>

namespace octa {
namespace {

constexpr std::size_t display_offset = 4;
constexpr std::size_t reverb_offset = 0x0a;
constexpr std::size_t preamp_offset = 0x15;
constexpr std::size_t mixer_offset = 0x58;

bool valid(InitializationTarget target) {
  return static_cast<std::uint8_t>(target) <=
         static_cast<std::uint8_t>(InitializationTarget::patchbay);
}

bool selects_mixer_a(InitializationTarget target) {
  return target == InitializationTarget::all || target == InitializationTarget::patchbay;
}

bool selected(std::size_t offset, InitializationTarget target) {
  switch (target) {
    case InitializationTarget::all: return offset >= display_offset;
    case InitializationTarget::preamps: return offset >= preamp_offset && offset < mixer_offset;
    case InitializationTarget::direct_mix: return offset >= mixer_offset;
    case InitializationTarget::reverb: return offset >= reverb_offset && offset < preamp_offset;
    case InitializationTarget::patchbay: return offset >= display_offset && offset < reverb_offset;
    default: return false;
  }
}

}

std::expected<std::vector<std::uint8_t>, InitializationError>
encode_initialization(InitializationTarget target) {
  if (!valid(target)) { return std::unexpected(InitializationError::target); }
  const std::array payload = {static_cast<std::uint8_t>(target)};
  auto frame = encode(Command::data, {0, 0, 0, 2}, payload);
  if (!frame) { return std::unexpected(InitializationError::state); }
  if (selects_mixer_a(target)) {
    constexpr std::array<std::uint8_t, 1> mixer_a = {0};
    const auto display = encode(Command::data, {0, 2, 0, 3}, mixer_a);
    if (!display) { return std::unexpected(InitializationError::state); }
    frame->insert(frame->end(), display->begin(), display->end());
  }
  return *frame;
}

std::expected<bool, InitializationError> initialization_scope_matches(const Message &before,
                                                                      const Message &after,
                                                                      InitializationTarget target) {
  if (!valid(target)) { return std::unexpected(InitializationError::target); }
  if (!validate_bulk_write(before) || !validate_bulk_write(after)) {
    return std::unexpected(InitializationError::state);
  }
  // TxDMix(0) does not necessarily change the corresponding bulk property.
  // Accept preservation or zero, while rejecting an unrelated new selection.
  if (selects_mixer_a(target) && after.payload[display_offset] != 0 &&
      after.payload[display_offset] != before.payload[display_offset]) {
    return false;
  }
  for (std::size_t offset = 0; offset < bulk_size; ++offset) {
    if (!selected(offset, target) && before.payload[offset] != after.payload[offset]) {
      return false;
    }
  }
  return true;
}

}
